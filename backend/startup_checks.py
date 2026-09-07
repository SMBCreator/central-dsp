"""Fail-fast проверки конфигурации при старте процесса.

Чеклист 6.1 / 6.11: пустой `CRON_SECRET` открывает `/cron/reset-daily`
любому снаружи, публичный dev-fallback `EVENT_TOKEN_SECRET` делает
win/imp/click-токены подделываемыми, `RTB_API_TOKEN` защищает фид биддера.
В production такие процессы не должны подниматься вообще — лучше упасть на
старте с понятным сообщением, чем торговать с открытыми ручками.

Вне production (dev, тесты, песочница) проверки только логируются.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from urllib.parse import urlsplit

log = logging.getLogger("startup_checks")

MIN_SECRET_LEN = 32

# Секреты, без которых production небезопасен. (name, purpose)
REQUIRED_SECRETS: tuple[tuple[str, str], ...] = (
    ("EVENT_TOKEN_SECRET", "подписывает win/imp/click event-токены (nurl/lurl)"),
    ("CRON_SECRET", "закрывает /cron/* (reset-daily, retention, ord-export)"),
    ("RTB_API_TOKEN", "закрывает фид биддера /rtb-feed/*"),
)

_PLACEHOLDER_PREFIXES = ("change_me", "changeme", "change-me", "replace_me", "todo", "xxx")
_WEAK_VALUES = frozenset(
    {"dev", "test", "secret", "password", "postgres", "admin", "root", "123456", "centraldsp"}
)


def is_production(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return env.get("APP_ENV", "").strip().lower() in ("production", "prod")


def _secret_problem(name: str, value: str | None) -> str | None:
    if not value or not value.strip():
        return f"{name} не задан"
    v = value.strip()
    low = v.lower()
    if low.startswith(_PLACEHOLDER_PREFIXES):
        return f"{name} — плейсхолдер из .env.example ({v[:12]}…)"
    if low in _WEAK_VALUES:
        return f"{name} — известное слабое значение"
    if len(v) < MIN_SECRET_LEN:
        return f"{name} короче {MIN_SECRET_LEN} символов (len={len(v)}); сгенерировать: openssl rand -hex 32"
    return None


def _database_url_problem(url: str | None) -> str | None:
    if not url:
        return "DATABASE_URL не задан"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "DATABASE_URL не парсится"
    pw = (parts.password or "").lower()
    if not pw:
        return None  # trust/peer-auth или секрет в другом месте — не наша зона
    if pw in _WEAK_VALUES or pw.startswith(_PLACEHOLDER_PREFIXES):
        return "DATABASE_URL: пароль Postgres — дефолтный/плейсхолдер"
    return None


def collect_problems(env: Mapping[str, str] | None = None) -> list[str]:
    """Список нарушений. Пустой список — конфигурация пригодна для production."""
    env = os.environ if env is None else env
    problems: list[str] = []
    for name, purpose in REQUIRED_SECRETS:
        value = env.get(name)
        if name == "EVENT_TOKEN_SECRET" and not value:
            # tokens.py принимает SESSION_SECRET как fallback — зеркалим.
            value = env.get("SESSION_SECRET")
        p = _secret_problem(name, value)
        if p:
            problems.append(f"{p} — {purpose}")
    p = _database_url_problem(env.get("DATABASE_URL"))
    if p:
        problems.append(p)
    p = registration_mode_problem(env)
    if p:
        problems.append(p)
    # Audit S-04: эти флаги — dev-лазейки. В production они не «предупреждение»,
    # а прямой путь к двойным бидам / поддельным win-нотисам.
    if _truthy(env.get("RTB_PYTHON_SERVING")):
        problems.append(
            "RTB_PYTHON_SERVING включён — Python-биддер торгует параллельно Go (двойные биды на один request); "
            "в production торгует только bidder-go"
        )
    if env.get("RTB_ALLOW_UNSIGNED_EVENTS", "").strip() == "1":
        problems.append(
            "RTB_ALLOW_UNSIGNED_EVENTS=1 — принимаются неподписанные win/imp/click (любой может списать бюджет); "
            "только dev/симулятор"
        )
    return problems


# --- REGISTRATION_MODE (audit S-04) -------------------------------------------
# Раньше дефолт был `open`, а любое неизвестное значение (`Closed`, `off`,
# опечатка) тоже вело в ветку «открыто». Теперь: enum, дефолт closed,
# мусор — fail-fast в production и closed + warning вне его.

REGISTRATION_MODES = ("open", "closed")
REGISTRATION_MODE_DEFAULT = "closed"


def registration_mode_problem(env: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    raw = env.get("REGISTRATION_MODE")
    if raw is None or not raw.strip():
        return None
    if raw.strip().lower() not in REGISTRATION_MODES:
        return f"REGISTRATION_MODE={raw.strip()!r} не распознан; допустимо: {', '.join(REGISTRATION_MODES)}"
    return None


def registration_mode(env: Mapping[str, str] | None = None) -> str:
    """Эффективный режим регистрации. Неизвестное значение = closed (fail-closed)."""
    env = os.environ if env is None else env
    raw = (env.get("REGISTRATION_MODE") or "").strip().lower()
    return raw if raw in REGISTRATION_MODES else REGISTRATION_MODE_DEFAULT


def collect_warnings(env: Mapping[str, str] | None = None) -> list[str]:
    """Не фатально, но в production стоит знать."""
    env = os.environ if env is None else env
    warnings: list[str] = []
    if registration_mode(env) == "open":
        warnings.append("REGISTRATION_MODE=open — любой может создать аккаунт (чеклист 6.2)")
    if env.get("ALLOW_MOCK_FALLBACK", "").strip() == "1":
        warnings.append("ALLOW_MOCK_FALLBACK=1 — прокси фронта отдаёт мок-данные при недоступном API")
    if env.get("ORD_FEED_GATE", "").strip().lower() == "off":
        warnings.append("ORD_FEED_GATE=off игнорируется в production: erid-гейт фида всегда включён (L-01)")
    if env.get("LAUNCH_GATE", env.get("ORD_LAUNCH_GUARD", "")).strip().lower() == "off":
        warnings.append("LAUNCH_GATE=off игнорируется в production: launch gate кампаний всегда включён (L-01)")
    if not _truthy(env.get("ORD_REQUIRED", "1")):
        warnings.append(
            "ORD_REQUIRED=0 — production без ОРД: регистрация креативов недоступна, "
            "фид отдаёт только группы с гео без РФ (L-01)"
        )
    if not env.get("PUBLIC_ORIGINS", "").strip():
        warnings.append(
            "PUBLIC_ORIGINS не задан — Origin-гейт (S-01) сверяет источник только с Host/X-Forwarded-Host; "
            "задайте публичные origin'ы UI через запятую"
        )
    if env.get("AUTH_COOKIE_SAMESITE", "").strip().lower() == "none":
        warnings.append(
            "AUTH_COOKIE_SAMESITE=none — сессионная cookie уходит с cross-site запросами (только для iframe-превью)"
        )
    if not (env.get("PUBLIC_BIDDER_BASE_URL") or env.get("PUBLIC_BASE_URL") or "").strip():
        warnings.append(
            "PUBLIC_BIDDER_BASE_URL/PUBLIC_BASE_URL не заданы — UI не может выдать SSP публичный URL "
            "Go-биддера (/rtb/bid/{key}); партнёры получат неверный адрес (R-01)"
        )
    if not (env.get("PUBLIC_API_BASE_URL") or env.get("PUBLIC_BASE_URL") or "").strip():
        warnings.append(
            "PUBLIC_API_BASE_URL/PUBLIC_BASE_URL не заданы — pixel-скрипт не выдаётся (404): "
            "beacon'у нужен внешний HTTPS-адрес /api/pixel/collect (E-05)"
        )
    return warnings


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


# --- ОРД (audit L-01) ---------------------------------------------------------
# Mock-ОРД в production выдаёт фиктивные erid на боевой трафик — это штраф по
# 38-ФЗ, а не dev-удобство. Production обязан иметь ключ ОРД (БД или env),
# либо явно объявить ORD_REQUIRED=0 (стенд без РФ-трафика) — тогда фид всё
# равно закрыт для РФ, просто процесс не падает на старте.


def ord_required(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return _truthy(env.get("ORD_REQUIRED", "1"))


async def check_ord_configured(*, production: bool, env: Mapping[str, str] | None = None) -> None:
    """В production без ключа ОРД и без ORD_REQUIRED=0 — RuntimeError."""
    if not production:
        return
    from ord.adapter import get_ord_settings

    cfg = await get_ord_settings()
    if cfg.get("api_key"):
        log.info("ord configured: provider=ozon source=%s base_url=%s", cfg.get("source"), cfg.get("base_url"))
        return
    if not ord_required(env):
        log.warning("ord: не настроен, ORD_REQUIRED=0 — работаем без маркировки (только не-РФ гео)")
        return
    raise RuntimeError(
        "ОРД не настроен для APP_ENV=production: задайте ORD_OZON_API_KEY (или ключ в Настройках платформы) "
        "либо ORD_REQUIRED=0, если стенд не торгует РФ-трафиком. Mock-erid в production запрещены (audit L-01)."
    )


# --- Redis durability (audit M-03) -------------------------------------------
# Redis хранит деньги торгов (spend:*, резервы, dedup, bidmeta, rtb:events).
# Любая eviction-политика означает «Redis может забыть расход» => перерасход
# бюджета. В production допустим только noeviction; AOF/RDB — предупреждение.

SAFE_MAXMEMORY_POLICY = "noeviction"


def redis_durability_problems(info: Mapping[str, str], config: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """(problems, warnings) по результатам INFO persistence и CONFIG GET.

    `info` — словарь INFO (ключи aof_enabled, rdb_last_save_time…),
    `config` — {'maxmemory-policy': ..., 'maxmemory': ..., 'appendonly': ...}.
    """
    problems: list[str] = []
    warnings: list[str] = []
    policy = str(config.get("maxmemory-policy", "")).strip().lower()
    if policy and policy != SAFE_MAXMEMORY_POLICY:
        problems.append(
            f"Redis maxmemory-policy={policy}: eviction может удалить ключи spend:*/bidmeta и открыть перерасход "
            f"бюджета; требуется {SAFE_MAXMEMORY_POLICY}"
        )
    aof = str(config.get("appendonly", info.get("aof_enabled", ""))).strip().lower()
    if aof in ("no", "0", "false", ""):
        rdb_save = str(info.get("rdb_last_save_time", "")).strip()
        if not rdb_save or rdb_save == "0":
            warnings.append("Redis без AOF и без RDB-снапшотов: рестарт обнулит дневные расходы (audit M-03)")
        else:
            warnings.append("Redis без AOF (только RDB): при рестарте возможна потеря расхода до последнего снапшота")
    return problems, warnings


async def check_redis_durability(redis_client, *, production: bool) -> None:
    """Опросить живой Redis. В production при eviction-политике — RuntimeError.

    Ошибка самого опроса (Redis недоступен, CONFIG запрещён у managed-провайдера)
    не фатальна: логируется, чтобы не блокировать старт из-за прав на CONFIG.
    """
    try:
        cfg: dict[str, str] = {}
        for name in ("maxmemory-policy", "maxmemory", "appendonly"):
            got = await redis_client.config_get(name)
            cfg.update({str(k): str(v) for k, v in dict(got).items()})
        info = await redis_client.info("persistence")
    except Exception as exc:  # noqa: BLE001 — managed Redis может запрещать CONFIG
        log.warning("redis durability check skipped: %s", exc)
        return
    problems, warnings = redis_durability_problems({str(k): str(v) for k, v in info.items()}, cfg)
    for w in warnings:
        log.warning("redis: %s", w)
    if problems:
        if production:
            raise RuntimeError("Небезопасный Redis для APP_ENV=production:\n  - " + "\n  - ".join(problems))
        for p in problems:
            log.warning("redis (dev, не фатально): %s", p)
    else:
        log.info("redis durability ok: policy=%s appendonly=%s", cfg.get("maxmemory-policy"), cfg.get("appendonly"))


def assert_production_config(env: Mapping[str, str] | None = None) -> None:
    """Вызывать на старте процесса. В production при нарушениях — RuntimeError."""
    env = os.environ if env is None else env
    problems = collect_problems(env)
    warnings = collect_warnings(env)
    prod = is_production(env)
    for w in warnings:
        log.warning("config: %s", w)
    if not problems:
        return
    if prod:
        raise RuntimeError(
            "Небезопасная конфигурация для APP_ENV=production:\n  - " + "\n  - ".join(problems)
        )
    for p in problems:
        log.warning("config (dev, не фатально): %s", p)
