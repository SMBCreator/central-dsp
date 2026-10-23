"""Настройки аккаунта: валюта/наценка на курс, глобальные white/blacklist площадок.

app_settings — key/value (jsonb) на пользователя; domain_lists — глобальные
листы доменов, применяются биддером ко всем кампаниям аккаунта. Кнопка
«в блэклист» из статистики бьёт в POST /settings/domain-lists с source=stats_click.
"""
import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import fx as fx_mod
from auth import current_user
from db import get_pool
from rbac import require_settings_write

router = APIRouter(prefix="/settings", tags=["settings"])

DEFAULTS: dict = {
    "currency": "RUB",           # кабинет всегда в рублях
    "fx_markup_percent": 3.0,    # наценка к курсу ЦБ на волатильность
}


def public_bidder_base_url(env=None) -> str | None:
    """Публичный базовый URL Go-биддера для SSP (audit R-01).

    SSP должны получать `${PUBLIC_BIDDER_BASE_URL}/rtb/bid/{endpoint_key}` —
    endpoint bidder-go, а не `/api/rtb/bid/...` backend'а (Python-биддер в
    production выключен, A7/S-04). Fallback — PUBLIC_BASE_URL: в штатной
    топологии Caddy маршрутизирует `/rtb/*` на bidder-go с того же домена.
    None = не настроено; UI покажет предупреждение, а не «угадает» origin.
    """
    env = os.environ if env is None else env
    for name in ("PUBLIC_BIDDER_BASE_URL", "PUBLIC_BASE_URL"):
        v = (env.get(name) or "").strip().rstrip("/")
        if v:
            return v
    return None


def public_api_base_url(env=None) -> str | None:
    """Публичный базовый URL backend-API, как его видит браузер (audit E-05).

    Pixel-скрипт на сайте клиента шлёт sendBeacon на `${base}/pixel/collect`, и
    этот адрес обязан быть внешним HTTPS-адресом, который прокси заводит в
    backend. Раньше брался `request.base_url`: за Caddy это внутренний
    `http://backend:8000` или домен без `/api` — beacon уходил во frontend.

    Порядок: `PUBLIC_API_BASE_URL` (например `https://dsp.example/api`), иначе
    `PUBLIC_BASE_URL` + `/api` (штатный Caddy: `handle /api/*` → backend).
    None = не настроено: скрипт не выдаём (404), UI показывает предупреждение.
    """
    env = os.environ if env is None else env
    explicit = (env.get("PUBLIC_API_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    base = (env.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base:
        return f"{base}/api"
    return None


@router.get("/public")
async def get_public_settings(user: dict = Depends(current_user)):
    """Не-секретные параметры деплоя, нужные UI (любая роль)."""
    base = public_bidder_base_url()
    api_base = public_api_base_url()
    return {
        "bidder_base_url": base,
        "rtb_bid_url_template": f"{base}/rtb/bid/{{endpoint_key}}" if base else None,
        "ssp_token_header": "X-CentralDSP-Token",
        "api_base_url": api_base,
        "pixel_script_url_template": f"{api_base}/pixel/script/{{pixel_id}}.js" if api_base else None,
    }


async def _load_settings(user_id: str) -> dict:
    pool = await get_pool()
    rows = await pool.fetch("SELECT key, value FROM app_settings WHERE user_id = $1", user_id)
    out = dict(DEFAULTS)
    for r in rows:
        v = r["value"]
        out[r["key"]] = json.loads(v) if isinstance(v, str) else v
    return out


@router.get("")
async def get_settings(user: dict = Depends(current_user)):
    return await _load_settings(user["id"])


class SettingsPatch(BaseModel):
    fx_markup_percent: float | None = Field(default=None, ge=0, le=50)


@router.patch("")
async def patch_settings(body: SettingsPatch, user: dict = Depends(require_settings_write)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "Нет полей для обновления")
    pool = await get_pool()
    for k, v in updates.items():
        await pool.execute(
            """INSERT INTO app_settings (user_id, key, value) VALUES ($1, $2, $3::jsonb)
               ON CONFLICT (user_id, key)
                 DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
            user["id"], k, json.dumps(v),
        )
    return await _load_settings(user["id"])


@router.get("/fx")
async def fx_rate(currency: str = "USD", user: dict = Depends(current_user)):
    """Курс ЦБ + наценка аккаунта = effective_rate (по нему конвертируем $-закупку)."""
    rate = await fx_mod.get_rate(currency)
    if rate is None:
        raise HTTPException(503, "Курс недоступен — попробуйте позже")
    s = await _load_settings(user["id"])
    markup = float(s.get("fx_markup_percent") or 0)
    return {
        "currency": currency,
        "cbr_rate": round(rate, 4),
        "markup_percent": markup,
        "effective_rate": round(rate * (1 + markup / 100), 4),
    }


# --- Глобальные white/blacklist площадок -------------------------------------
class DomainIn(BaseModel):
    domain: str = Field(min_length=2, max_length=255)
    list_type: str = Field(default="black", pattern="^(black|white)$")
    source: str = Field(default="manual", pattern="^(manual|stats_click|rule)$")
    note: str | None = Field(default=None, max_length=300)


@router.get("/domain-lists")
async def list_domains(list_type: str | None = None, user: dict = Depends(current_user)):
    pool = await get_pool()
    if list_type:
        rows = await pool.fetch(
            "SELECT * FROM domain_lists WHERE user_id = $1 AND list_type = $2 ORDER BY created_at DESC",
            user["id"], list_type,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM domain_lists WHERE user_id = $1 ORDER BY created_at DESC", user["id"]
        )
    return [dict(r) | {"id": str(r["id"]), "user_id": str(r["user_id"]),
                       "created_at": r["created_at"].isoformat()} for r in rows]


@router.post("/domain-lists", status_code=201)
async def add_domain(body: DomainIn, user: dict = Depends(require_settings_write)):
    domain = body.domain.strip().lower()
    if "/" in domain or " " in domain:
        raise HTTPException(422, "Укажите домен без протокола и пути, например example.com")
    pool = await get_pool()
    row = await pool.fetchrow(
        """INSERT INTO domain_lists (user_id, domain, list_type, source, note)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (user_id, domain, list_type) DO UPDATE SET note = COALESCE(EXCLUDED.note, domain_lists.note)
           RETURNING *""",
        user["id"], domain, body.list_type, body.source, body.note,
    )
    return dict(row) | {"id": str(row["id"]), "user_id": str(row["user_id"]),
                        "created_at": row["created_at"].isoformat()}


@router.delete("/domain-lists/{item_id}", status_code=204)
async def remove_domain(item_id: str, user: dict = Depends(require_settings_write)):
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM domain_lists WHERE id = $1 AND user_id = $2", item_id, user["id"]
    )
    if result == "DELETE 0":
        raise HTTPException(404, "Запись не найдена")


# ------------------------------------------------------------------ ОРД Озон --
# Платформенные настройки ОРД: API-ключ хранится в platform_settings (jsonb),
# доступ на чтение/запись — только superadmin. Ключ наружу отдаётся маской.
from rbac import require_roles as _require_roles  # noqa: E402

_require_superadmin = _require_roles("superadmin")

_ORD_BASE_URLS = {
    "production": "https://api-ord.ozon.ru",
    "sandbox": "https://api-ord-sandbox.ozon.ru",  # docs/ord/swagger_ord_ozon.json
}


class OrdSettingsIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)
    environment: str = Field(default="production", pattern="^(production|sandbox)$")
    # URL рекламной системы для сводной площадки ОРС (ord_spec §7); пусто = не менять.
    platform_url: Optional[str] = Field(default=None, max_length=500)


def _mask(key: str) -> str:
    return key[:4] + "•" * 8 + key[-4:] if len(key) > 12 else "•" * len(key)


@router.get("/ord")
async def get_ord_settings_api(user: dict = Depends(_require_superadmin)):
    from ord.adapter import get_ord_settings, mock_allowed

    cfg = await get_ord_settings()
    if not cfg:
        if not mock_allowed():
            # Production: mock не выдаётся и не принимается (audit L-01) —
            # регистрация креативов недоступна, РФ-фид закрыт. UI показывает блокер.
            return {"configured": False, "provider": None, "blocking": True,
                    "note": "ОРД не настроен: регистрация креативов недоступна, показы в РФ "
                            "заблокированы. Сохраните API-ключ ОРД Озон."}
        return {"configured": False, "provider": "mock", "blocking": False,
                "note": "Ключ не задан — используется mock-ОРД (фиктивные erid, только dev)"}
    env = "sandbox" if "sandbox" in cfg["base_url"] else "production"
    return {"configured": True, "provider": "ozon", "environment": env,
            "base_url": cfg["base_url"], "api_key_masked": _mask(cfg["api_key"]),
            "platform_url": cfg.get("platform_url") or os.getenv("ORD_PLATFORM_URL") or "",
            "source": cfg.get("source", "db")}


@router.put("/ord")
async def put_ord_settings(body: OrdSettingsIn, user: dict = Depends(_require_superadmin)):
    pool = await get_pool()
    prev = await pool.fetchval("SELECT value FROM platform_settings WHERE key = 'ord_ozon'")
    prev_cfg = (prev if isinstance(prev, dict) else json.loads(prev)) if prev else {}
    platform_url = (body.platform_url or "").strip() or prev_cfg.get("platform_url") or ""
    if platform_url and not platform_url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL рекламной системы должен начинаться с http:// или https://")
    value = json.dumps({
        "api_key": body.api_key.strip(),
        "base_url": _ORD_BASE_URLS[body.environment],
        "platform_url": platform_url,
    })
    await pool.execute(
        """INSERT INTO platform_settings (key, value, updated_by)
           VALUES ('ord_ozon', $1::jsonb, $2)
           ON CONFLICT (key) DO UPDATE
             SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()""",
        value, user["id"],
    )
    return {"ok": True, "environment": body.environment,
            "api_key_masked": _mask(body.api_key.strip())}


@router.delete("/ord", status_code=204)
async def delete_ord_settings(user: dict = Depends(_require_superadmin)):
    pool = await get_pool()
    await pool.execute("DELETE FROM platform_settings WHERE key = 'ord_ozon'")


@router.post("/ord/test")
async def test_ord_connection(user: dict = Depends(_require_superadmin)):
    from ord.adapter import OrdError, OrdNotConfigured, get_ord_adapter

    try:
        adapter = await get_ord_adapter()
    except OrdNotConfigured as e:
        raise HTTPException(503, str(e))
    if adapter.provider == "mock":
        return {"ok": True, "provider": "mock",
                "note": "Ключ не задан — работает mock. Сохраните ключ и повторите."}
    try:
        await adapter.ping()
        return {"ok": True, "provider": "ozon"}
    except OrdError as e:
        raise HTTPException(502, str(e))


# ------------------------------------------- Throttling: datacenter-подсети --
# Платформенный список CIDR (/16, /24), которые предфильтр отсекает до полного
# цикла (prefilter.py, Часть C). Хранится в Redis SET; управление — admin+.
_require_platform_admin = _require_roles("superadmin", "admin")


class SubnetIn(BaseModel):
    cidr: str = Field(min_length=9, max_length=18, description="CIDR /16 или /24, напр. 45.155.204.0/24")


@router.get("/throttle/subnets")
async def list_dc_subnets(user: dict = Depends(_require_platform_admin)):
    import prefilter
    from cache import get_redis

    try:
        r = get_redis()
        members = sorted(await r.smembers(prefilter.DC_SUBNETS_KEY))
    except Exception:
        raise HTTPException(503, "Redis недоступен — список подсетей временно нечитаем")
    return {"prefixes": members, "count": len(members)}


@router.post("/throttle/subnets", status_code=201)
async def add_dc_subnet(body: SubnetIn, user: dict = Depends(_require_platform_admin)):
    import prefilter
    from cache import get_redis

    prefix = prefilter.cidr_to_prefix(body.cidr)
    if prefix is None:
        raise HTTPException(422, "Ожидается IPv4 CIDR с маской /16 или /24, напр. 45.155.204.0/24")
    try:
        r = get_redis()
        await r.sadd(prefilter.DC_SUBNETS_KEY, prefix)
    except Exception:
        raise HTTPException(503, "Redis недоступен — попробуйте позже")
    return {"ok": True, "prefix": prefix}


@router.delete("/throttle/subnets", status_code=204)
async def remove_dc_subnet(body: SubnetIn, user: dict = Depends(_require_platform_admin)):
    import prefilter
    from cache import get_redis

    prefix = prefilter.cidr_to_prefix(body.cidr)
    if prefix is None:
        raise HTTPException(422, "Ожидается IPv4 CIDR с маской /16 или /24")
    try:
        r = get_redis()
        removed = await r.srem(prefilter.DC_SUBNETS_KEY, prefix)
    except Exception:
        raise HTTPException(503, "Redis недоступен — попробуйте позже")
    if not removed:
        raise HTTPException(404, "Подсеть не найдена в списке")


# ----------------------------------------------------------- ML: статус слоя --
@router.get("/ml/status")
async def ml_status(user: dict = Depends(_require_platform_admin)):
    """Статус ML-слоя (Часть B): активная pCTR-модель и покрытие domain quality."""
    import json as _json

    pool = await get_pool()
    model = await pool.fetchrow(
        "SELECT version, trained_at, window_from, window_to, sample_events, metrics, params "
        "FROM ml_models WHERE model_type = 'pctr' AND status = 'active' "
        "ORDER BY version DESC LIMIT 1"
    )
    quality_agg = await pool.fetchrow(
        "SELECT count(*) AS domains, "
        "       count(*) FILTER (WHERE score < 0.5) AS flagged, "
        "       max(updated_at) AS last_update "
        "FROM domain_quality"
    )
    clean_campaigns = await pool.fetchval(
        "SELECT count(*) FROM campaigns WHERE clean_traffic_only AND status = 'active'"
    )
    out: dict = {
        "pctr_model": None,
        "domain_quality": {
            "domains_scored": int(quality_agg["domains"] or 0),
            "flagged": int(quality_agg["flagged"] or 0),
            "last_update": quality_agg["last_update"].isoformat() if quality_agg["last_update"] else None,
        },
        "clean_traffic_campaigns": int(clean_campaigns or 0),
    }
    if model:
        metrics = model["metrics"]
        if isinstance(metrics, str):
            metrics = _json.loads(metrics or "{}")
        params = model["params"]
        if isinstance(params, str):
            params = _json.loads(params or "{}")
        out["pctr_model"] = {
            "version": int(model["version"]),
            "trained_at": model["trained_at"].isoformat() if model["trained_at"] else None,
            "window_from": model["window_from"].isoformat() if model["window_from"] else None,
            "window_to": model["window_to"].isoformat() if model["window_to"] else None,
            "sample_events": int(model["sample_events"] or 0),
            "metrics": metrics,
            "params": params,
        }
    return out


# ---- Технические алерты (audit C4b / N-02): куда слать письма ----------------
# Хранится в platform_settings key='alerting'. Пустой список адресов = письма
# уходят на email всех активных суперадминов (см. alerting.resolve_recipients).
import alerting as _alerting  # noqa: E402
import notify_outbox as _notify_outbox  # noqa: E402


class AlertingSettingsIn(BaseModel):
    emails: list[str] = Field(default_factory=list, max_length=20)
    email_min_severity: str = Field(default="warning", pattern="^(info|warning|critical)$")
    email_on_resolve: bool = True


async def _alerting_view(pool) -> dict:
    settings = await _alerting.load_settings(pool)
    recipients, source = await _alerting.resolve_recipients(pool, settings)
    return {
        **settings,
        "effective_recipients": recipients,
        "recipients_source": source,
        "smtp_configured": _notify_outbox.is_configured(),
        "webhook_secret_configured": bool(_alerting.webhook_secret()),
    }


@router.get("/alerting")
async def get_alerting_settings(user: dict = Depends(_require_superadmin)):
    return await _alerting_view(await get_pool())


@router.put("/alerting")
async def put_alerting_settings(body: AlertingSettingsIn, user: dict = Depends(_require_superadmin)):
    try:
        settings = _alerting.normalise_settings(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    pool = await get_pool()
    await _alerting.save_settings(pool, settings, user["id"])
    return await _alerting_view(pool)


@router.post("/alerting/test")
async def send_test_alert_email(user: dict = Depends(_require_superadmin)):
    """Тестовое письмо через тот же outbox/SMTP, что и настоящие алерты."""
    pool = await get_pool()
    recipients, source = await _alerting.resolve_recipients(pool)
    if not recipients:
        raise HTTPException(400, "Нет адресов: добавьте email в настройках или укажите email у суперадмина")
    queued = await _notify_outbox.enqueue_email(
        pool, recipients=recipients, kind="tech_alert_test", source="settings",
        subject="Тестовое письмо: технические алерты",
        body_text="Если вы читаете это письмо — Central DSP умеет доставлять технические алерты на этот адрес.\n",
        body_html="<p style='font:14px system-ui'>Если вы читаете это письмо — Central DSP умеет "
                  "доставлять технические алерты на этот адрес.</p>",
    )
    return {"queued": queued, "recipients": recipients, "recipients_source": source,
            "smtp_configured": _notify_outbox.is_configured()}
