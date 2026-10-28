"""ID sync / cookie matching (план predeploy, раздел A; docs/ID_SYNC.md).

Связывает ID пользователя у SSP с нашим `dsp_uid`. Не даёт доступа к чужим
аудиториям — только стабильный ключ для frequency cap и ретаргетинга между SSP.

Пока ID_SYNC_ENABLED выключен, модуль никто не вызывает: ручки /sync/* отвечают
404, биддер идёт по прежнему пути deviceIDOf.

Хранение (A.4): PostgreSQL id_sync_map — durable, Redis
idsync:{partner_id}:{hex(sha256(salt||ssp_uid))} -> dsp_uid — горячая копия для
биддера (TTL = expires_at). Чужой ID в открытом виде нигде не хранится и не
логируется.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from redis.exceptions import RedisError

from cache import get_redis

log = logging.getLogger("id_sync")

REDIS_PREFIX = "idsync:"
COOKIE_NAME = "dsp_uid"
# Значение cookie после opt-out: новый dsp_uid не выдаётся, соответствия не создаются.
OPTOUT_VALUE = "optout"
SIGNATURE_TTL_SECONDS = 600
DEFAULT_TTL_DAYS = 90
MAX_TTL_DAYS = 395
MAX_SSP_UID_LEN = 128

# Печатные символы без разделителей HTTP/URL-инъекций: CR/LF, пробелы, кавычки,
# <>, ;, %, &, # и прочее отсекаются. Хватает для UUID, base64url, hex, «a.b:c».
_SSP_UID_RE = re.compile(r"^[A-Za-z0-9._~:@=+/\-]{1,128}$")
_DSP_UID_RE = re.compile(r"^[0-9a-f]{32}$")
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_PARAM_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_ID_SOURCE_RE = re.compile(r"^(buyeruid|user_id|ifa|eids:[a-z0-9][a-z0-9.\-]{0,63})$")
DSP_UID_MACRO = "{dsp_uid}"


def enabled() -> bool:
    return (os.environ.get("ID_SYNC_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def cookie_max_age() -> int:
    try:
        days = int(os.environ.get("ID_SYNC_COOKIE_DAYS", str(DEFAULT_TTL_DAYS)))
    except ValueError:
        days = DEFAULT_TTL_DAYS
    return max(1, min(days, MAX_TTL_DAYS)) * 86400


def _secret() -> bytes:
    value = os.environ.get("ID_SYNC_SECRET") or ""
    if not value:
        # Без секрета подписанные /out-ссылки не выпускаются и не принимаются.
        raise RuntimeError("ID_SYNC_SECRET is not configured")
    return value.encode()


# --- Конфиг партнёра (A.5) ----------------------------------------------------

@dataclass
class PartnerIdentity:
    sync_enabled: bool = False
    sync_in_param: str = "ssp_uid"
    sync_callback_url_template: str | None = None
    sync_redirect_hosts: list[str] = field(default_factory=list)
    id_source: str = "user_id"
    ttl_days: int = DEFAULT_TTL_DAYS
    requires_consent: bool = True

    @classmethod
    def from_json(cls, raw: dict | None) -> "PartnerIdentity":
        raw = raw or {}
        hosts = raw.get("sync_redirect_hosts") or []
        return cls(
            sync_enabled=bool(raw.get("sync_enabled", False)),
            sync_in_param=str(raw.get("sync_in_param") or "ssp_uid"),
            sync_callback_url_template=raw.get("sync_callback_url_template") or None,
            sync_redirect_hosts=[str(h).lower() for h in hosts if isinstance(h, str)],
            id_source=str(raw.get("id_source") or "user_id"),
            ttl_days=int(raw.get("ttl_days") or DEFAULT_TTL_DAYS),
            requires_consent=bool(raw.get("requires_consent", True)),
        )


def validate_identity_config(cfg: dict) -> dict:
    """Нормализует и проверяет конфиг из API. ValueError — понятная причина."""
    param = str(cfg.get("sync_in_param") or "ssp_uid").strip().lower()
    if not _PARAM_RE.match(param):
        raise ValueError("sync_in_param: латиница/цифры/_, начинается с буквы, до 32 символов")
    hosts = []
    for h in cfg.get("sync_redirect_hosts") or []:
        h = str(h).strip().lower().rstrip(".")
        if not h:
            continue
        if not _HOST_RE.match(h):
            raise ValueError(f"sync_redirect_hosts: «{h}» — не доменное имя (без схемы, пути и порта)")
        hosts.append(h)
    if len(hosts) > 16:
        raise ValueError("sync_redirect_hosts: не больше 16 доменов")
    template = (cfg.get("sync_callback_url_template") or "").strip() or None
    if template:
        if len(template) > 1024:
            raise ValueError("sync_callback_url_template длиннее 1024 символов")
        if DSP_UID_MACRO not in template:
            raise ValueError("sync_callback_url_template должен содержать макрос {dsp_uid}")
        host = _template_host(template)
        if host is None:
            raise ValueError("sync_callback_url_template: нужен абсолютный https:// URL")
        if host not in hosts:
            raise ValueError(f"sync_callback_url_template: домен {host} не входит в sync_redirect_hosts")
    id_source = str(cfg.get("id_source") or "user_id").strip()
    if not _ID_SOURCE_RE.match(id_source):
        raise ValueError("id_source: buyeruid | user_id | ifa | eids:<source>")
    try:
        raw_ttl = cfg.get("ttl_days")
        ttl = DEFAULT_TTL_DAYS if raw_ttl is None or raw_ttl == "" else int(raw_ttl)
    except (TypeError, ValueError) as exc:
        raise ValueError("ttl_days — целое число дней") from exc
    if not 1 <= ttl <= MAX_TTL_DAYS:
        raise ValueError(f"ttl_days: от 1 до {MAX_TTL_DAYS}")
    sync_enabled = bool(cfg.get("sync_enabled", False))
    if sync_enabled and id_source in ("buyeruid", "ifa"):
        # buyeruid сопоставляет сам SSP, ifa — не cookie: обратный sync не нужен.
        raise ValueError("sync_enabled имеет смысл только для id_source = user_id или eids:<source>")
    return {
        "sync_enabled": sync_enabled,
        "sync_in_param": param,
        "sync_callback_url_template": template,
        "sync_redirect_hosts": hosts,
        "id_source": id_source,
        "ttl_days": ttl,
        "requires_consent": bool(cfg.get("requires_consent", True)),
    }


def _template_host(template: str) -> str | None:
    try:
        parts = urlsplit(template.replace(DSP_UID_MACRO, "x"))
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    return parts.hostname.lower()


def build_redirect(identity: PartnerIdentity, dsp_uid: str) -> str | None:
    """URL SSP с нашим dsp_uid или None, если шаблона нет / домен не в allowlist.

    Шаблон проверяется при сохранении, но allowlist сверяется ещё раз здесь:
    защита от правки JSON в обход API (open redirect)."""
    template = identity.sync_callback_url_template
    if not template or not _DSP_UID_RE.match(dsp_uid):
        return None
    host = _template_host(template)
    if host is None or host not in identity.sync_redirect_hosts:
        return None
    url = template.replace(DSP_UID_MACRO, quote(dsp_uid, safe=""))
    if "\r" in url or "\n" in url:
        return None
    return url


# --- Валидация входа ----------------------------------------------------------

def valid_ssp_uid(value: str | None) -> bool:
    return bool(value) and len(value) <= MAX_SSP_UID_LEN and bool(_SSP_UID_RE.match(value)) and not set(value) <= {"0", "-"}


def valid_dsp_uid(value: str | None) -> bool:
    return bool(value) and bool(_DSP_UID_RE.match(value))


def new_dsp_uid() -> str:
    """128 бит из CSPRNG; ни от чего чужого не производное (A.3)."""
    return secrets.token_hex(16)


def new_salt() -> str:
    return secrets.token_hex(32)


def hash_ssp_uid(salt: str, ssp_uid: str) -> bytes:
    """sha256(salt || ssp_uid). Та же формула в bidder-go (rtb/identity.go)."""
    return hashlib.sha256(salt.encode() + ssp_uid.encode()).digest()


def redis_key(partner_id: str, uid_hash: bytes) -> str:
    return f"{REDIS_PREFIX}{partner_id}:{uid_hash.hex()}"


def hash_for_log(value: str) -> str:
    """Чужой ID в логах — только хэшем (правило 7 плана)."""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


# --- Согласие -------------------------------------------------------------------

def consent_scope(identity: PartnerIdentity, gdpr: str | None, gdpr_consent: str | None,
                  us_privacy: str | None) -> str | None:
    """Метка основания для записи или None — mapping создавать нельзя.

    - us_privacy с отказом от продажи (3-й символ 'Y') — отказ всегда;
    - gdpr=1 без строки согласия — отказ всегда;
    - партнёр requires_consent — нужна непустая строка согласия;
    - иначе — «none» (SSP не передаёт сигналов согласия, партнёр это допускает)."""
    gdpr = (gdpr or "").strip()
    consent = (gdpr_consent or "").strip()
    usp = (us_privacy or "").strip().upper()
    if len(usp) >= 3 and usp[2] == "Y":
        return None
    if len(consent) > 2048:
        return None
    if gdpr == "1" and not consent:
        return None
    if consent:
        return "gdpr" if gdpr == "1" else "consent"
    if identity.requires_consent:
        return None
    return "none"


# --- Подпись /out (A.3: HMAC, ts, TTL 10 мин, без replay) -----------------------

def sign(partner_key: str, ts: int) -> str:
    return hmac.new(_secret(), f"{partner_key}|{ts}".encode(), hashlib.sha256).hexdigest()


def signed_out_params(partner_key: str, now: float | None = None) -> dict:
    ts = int(now if now is not None else time.time())
    return {"ts": str(ts), "sig": sign(partner_key, ts)}


def verify_signature(partner_key: str, ts: str | None, sig: str | None, now: float | None = None) -> bool:
    if not ts or not sig or not ts.isdigit() or len(sig) != 64:
        return False
    now = time.time() if now is None else now
    ts_i = int(ts)
    if ts_i > now + 30 or now - ts_i > SIGNATURE_TTL_SECONDS:
        return False
    try:
        expected = sign(partner_key, ts_i)
    except RuntimeError:
        return False
    return hmac.compare_digest(expected, sig)


async def claim_signature(sig: str) -> bool:
    """Одноразовость подписи: повтор той же ссылки в окне TTL отклоняется.
    Redis недоступен — fail-closed (редирект не делаем, отвечаем gif)."""
    try:
        return bool(await get_redis().set(f"idsync:sig:{sig}", 1, nx=True, ex=SIGNATURE_TTL_SECONDS))
    except RedisError:
        log.warning("id_sync: redis unavailable, signature replay check failed closed")
        return False


# --- Rate limit (A.3) -------------------------------------------------------------

def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


async def rate_limited(ip: str, partner_key: str) -> bool:
    """Скользящая минута по IP и по партнёру. Redis недоступен — не лимитируем
    (fail-open: sync не трогает деньги), но и mapping в Redis тогда не запишется."""
    minute = int(time.time() // 60)
    ip_key = f"idsync:rl:ip:{hashlib.sha256(ip.encode()).hexdigest()[:16]}:{minute}"
    p_key = f"idsync:rl:p:{partner_key}:{minute}"
    try:
        pipe = get_redis().pipeline()
        pipe.incr(ip_key)
        pipe.expire(ip_key, 120)
        pipe.incr(p_key)
        pipe.expire(p_key, 120)
        ip_n, _, p_n, _ = await pipe.execute()
    except RedisError:
        return False
    return ip_n > _int_env("ID_SYNC_IP_RPM", 120) or p_n > _int_env("ID_SYNC_PARTNER_RPM", 60000)


# --- Хранилище (A.4) -----------------------------------------------------------------

@dataclass
class Partner:
    id: str
    identity: PartnerIdentity
    salt: str | None


async def load_partner(pool, partner_key: str) -> Partner | None:
    """partner_key — UUID партнёра. Неизвестный/выключенный — None (ответ тот же gif)."""
    try:
        uuid.UUID(partner_key)
    except ValueError:
        return None
    row = await pool.fetchrow(
        "SELECT id, identity, id_sync_salt, status FROM supply_partners WHERE id = $1", partner_key
    )
    if not row or row["status"] not in ("active", "testing") or not row["id_sync_salt"]:
        return None
    raw = row["identity"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    identity = PartnerIdentity.from_json(raw)
    if not identity.sync_enabled:
        return None
    return Partner(id=str(row["id"]), identity=identity, salt=row["id_sync_salt"])


async def existing_dsp_uid(pool, partner: Partner, uid_hash: bytes) -> str | None:
    return await pool.fetchval(
        "SELECT dsp_uid FROM id_sync_map WHERE partner_id = $1 AND ssp_uid_hash = $2 AND expires_at > now()",
        partner.id, uid_hash,
    )


async def store_mapping(pool, partner: Partner, uid_hash: bytes, dsp_uid: str, scope: str) -> bool:
    """PG (durable) -> Redis. True — соответствие новое (для метрики created)."""
    expires = datetime.now(timezone.utc) + timedelta(days=partner.identity.ttl_days)
    created = await pool.fetchval(
        """INSERT INTO id_sync_map (partner_id, ssp_uid_hash, dsp_uid, consent_scope, expires_at)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (partner_id, ssp_uid_hash) DO UPDATE
               SET dsp_uid = EXCLUDED.dsp_uid, consent_scope = EXCLUDED.consent_scope,
                   last_seen_at = now(), expires_at = EXCLUDED.expires_at
           RETURNING (xmax = 0)""",
        partner.id, uid_hash, dsp_uid, scope, expires,
    )
    try:
        await get_redis().set(redis_key(partner.id, uid_hash), dsp_uid, ex=partner.identity.ttl_days * 86400)
    except RedisError:
        # Биддер не увидит соответствие до rebuild_idsync; запрос всё равно
        # идентифицируется по user.id в namespace партнёра — не no-bid.
        log.warning("id_sync: redis write failed, mapping only in postgres", extra={"partner_id": partner.id})
    return bool(created)


async def delete_by_dsp_uid(pool, dsp_uid: str) -> int:
    rows = await pool.fetch(
        "DELETE FROM id_sync_map WHERE dsp_uid = $1 RETURNING partner_id, ssp_uid_hash", dsp_uid
    )
    if rows:
        try:
            await get_redis().delete(*[redis_key(str(r["partner_id"]), bytes(r["ssp_uid_hash"])) for r in rows])
        except RedisError:
            log.warning("id_sync: redis delete failed; keys expire by TTL, rebuild_idsync skips deleted rows")
    return len(rows)


async def purge_expired(pool, batch: int = 10000) -> int:
    """Просроченные строки PG. Redis-ключи истекают сами (TTL = expires_at)."""
    total = 0
    while True:
        n = await pool.fetchval(
            """WITH gone AS (
                   DELETE FROM id_sync_map WHERE ctid IN (
                       SELECT ctid FROM id_sync_map WHERE expires_at <= now() LIMIT $1)
                   RETURNING 1)
               SELECT count(*) FROM gone""",
            batch,
        )
        total += int(n or 0)
        if not n or n < batch:
            return total


async def rebuild_redis(pool, batch: int = 5000) -> int:
    """Досев Redis из PG (после потери Redis). Идемпотентно: SET с остаточным TTL."""
    written = 0
    last_pid, last_hash = None, b""
    r = get_redis()
    while True:
        rows = await pool.fetch(
            """SELECT partner_id, ssp_uid_hash, dsp_uid,
                      GREATEST(1, EXTRACT(EPOCH FROM (expires_at - now()))::bigint) AS ttl
               FROM id_sync_map
               WHERE expires_at > now()
                 AND ($1::uuid IS NULL OR (partner_id, ssp_uid_hash) > ($1::uuid, $2::bytea))
               ORDER BY partner_id, ssp_uid_hash
               LIMIT $3""",
            last_pid, last_hash, batch,
        )
        if not rows:
            return written
        pipe = r.pipeline()
        for row in rows:
            pipe.set(redis_key(str(row["partner_id"]), bytes(row["ssp_uid_hash"])), row["dsp_uid"], ex=int(row["ttl"]))
        await pipe.execute()
        written += len(rows)
        last_pid, last_hash = rows[-1]["partner_id"], bytes(rows[-1]["ssp_uid_hash"])
