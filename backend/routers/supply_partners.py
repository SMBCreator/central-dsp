"""SSP-партнёры: онбординг, управление токеном и автопроверка подключения.

Создание/изменение/удаление — только superadmin/admin (require_admin):
подключение SSP — платформенная операция уровня администратора.
Чтение — любой платформенный пользователь (страница /supply).
"""

import json
import os
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth import current_user
from geo_norm import geo_normalize
from rbac import require_admin
from db import get_pool
from schemas import SupplyPartnerIn, SupplyPartnerPatch
from stats_math import derive, money, pct, spend_expr
import id_sync
import ssp_check
import ssp_tokens

router = APIRouter(prefix="/supply-partners", tags=["supply-partners"])

_JSONB_FIELDS = {"allowed_ad_types", "ip_whitelist", "identity"}


def _serialize(row) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    d["user_id"] = str(d["user_id"])
    d["bid_floor"] = float(d["bid_floor"])
    for k in ("allowed_ad_types", "ip_whitelist", "last_check", "identity"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    # В БД только хэш (миграция 032); наружу — признак и хвост для сверки
    token_hash = d.pop("auth_token_hash", None)
    hint = d.pop("auth_token_hint", None)
    d.pop("auth_token", None)  # на случай прогона до миграции 032
    d.pop("token_prev_hash", None)  # хэш предыдущего токена наружу не нужен
    d.pop("id_sync_salt", None)  # соль хэша чужих ID наружу не нужна
    d["has_auth_token"] = bool(token_hash)
    d["auth_token_hint"] = f"…{hint}" if token_hash and hint else None
    return d


# Статусы, в которых endpoint партнёра реально принимает bid request'ы
# (bidder_feed.py отдаёт их в Go; rtb.py торгует). Переход сюда без выданного
# токена запрещён — audit S-06: биддер отвечает 403 партнёру без хэша, так что
# «включить без токена» = включить заведомо неработающий endpoint.
_SERVING_STATUSES = ("testing", "active")


def _require_token_for_serving(status: str | None, token_hash: str | None) -> None:
    if status in _SERVING_STATUSES and not token_hash:
        raise HTTPException(
            status_code=422,
            detail="Сначала выпустите токен партнёра (POST /supply-partners/{id}/token): "
                   "без токена биддер отвечает 403 и перевод в testing/active бессмыслен",
        )


def _validated_identity(cfg: dict) -> dict:
    try:
        return id_sync.validate_identity_config(cfg)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"ID sync: {exc}") from exc


def _validated_whitelist(items: list[str]) -> list[str]:
    try:
        return ssp_tokens.validate_ip_whitelist(items)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
async def list_partners(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT * FROM supply_partners WHERE user_id = $1 ORDER BY created_at DESC", user["id"]
    )
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_partner(body: SupplyPartnerIn, user: dict = Depends(require_admin)):
    pool = await get_pool()
    endpoint_key = secrets.token_urlsafe(16)
    whitelist = _validated_whitelist(body.ip_whitelist)
    identity = _validated_identity(body.identity.model_dump()) if body.identity else None
    # billing_event/freq_cap_on_win: не передано -> DEFAULT БД (046: новые
    # партнёры — burl; 043: freq cap по imp).
    row = await pool.fetchrow(
        """
        INSERT INTO supply_partners
            (user_id, name, endpoint_key, auction_type, bid_floor, qps_limit, allowed_ad_types, notes,
             protocol_version, currency, tmax_ms, ip_whitelist, seat_id, contact_email, status,
             billing_event, freq_cap_on_win, identity, id_sync_salt)
        VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12::jsonb,$13,$14,'draft',
                COALESCE($15, 'burl'), COALESCE($16, false), $17::jsonb, $18) RETURNING *
        """,
        user["id"], body.name, endpoint_key, body.auction_type, body.bid_floor,
        body.qps_limit, json.dumps(body.allowed_ad_types), body.notes,
        body.protocol_version, body.currency, body.tmax_ms,
        json.dumps(whitelist), body.seat_id, body.contact_email,
        body.billing_event, body.freq_cap_on_win,
        json.dumps(identity) if identity else None, id_sync.new_salt() if identity else None,
    )
    return _serialize(row)


@router.patch("/{partner_id}")
async def update_partner(partner_id: str, body: SupplyPartnerPatch, user: dict = Depends(require_admin)):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    pool = await get_pool()
    if "ip_whitelist" in updates:
        updates["ip_whitelist"] = _validated_whitelist(updates["ip_whitelist"])
    if "identity" in updates:
        updates["identity"] = _validated_identity(updates["identity"])
    if updates.get("status") in _SERVING_STATUSES:
        current = await pool.fetchrow(
            "SELECT auth_token_hash FROM supply_partners WHERE id = $1 AND user_id = $2",
            partner_id, user["id"],
        )
        if not current:
            raise HTTPException(status_code=404, detail="SSP-партнёр не найден")
        _require_token_for_serving(updates["status"], current["auth_token_hash"])
    sets, values = [], []
    for i, (k, v) in enumerate(updates.items(), start=3):
        if k in _JSONB_FIELDS:
            sets.append(f"{k} = ${i}::jsonb")
            values.append(json.dumps(v))
        else:
            sets.append(f"{k} = ${i}")
            values.append(v)
    if "identity" in updates:
        # Соль выдаётся один раз: смена соли обнулила бы все соответствия.
        sets.append(f"id_sync_salt = COALESCE(id_sync_salt, ${len(values) + 3})")
        values.append(id_sync.new_salt())
    row = await pool.fetchrow(
        f"UPDATE supply_partners SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
        partner_id, user["id"], *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")
    return _serialize(row)


def _sync_base_url() -> str:
    for var in ("ID_SYNC_BASE_URL", "PUBLIC_BASE_URL"):
        val = (os.environ.get(var) or "").strip().rstrip("/")
        if val:
            return val
    return ""


@router.get("/{partner_id}/sync-urls")
async def sync_urls(partner_id: str, user: dict = Depends(require_admin)):
    """URL для анкеты SSP (A.3): пиксель /in с макросом и одноразовая подписанная
    ссылка /out (живёт 10 минут) — для ручной проверки цикла."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT id, identity FROM supply_partners WHERE id = $1 AND user_id = $2", partner_id, user["id"]
    )
    if not row:
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")
    raw = row["identity"]
    identity = id_sync.PartnerIdentity.from_json(json.loads(raw) if isinstance(raw, str) else raw)
    base = _sync_base_url()
    key = str(row["id"])
    out_url = None
    try:
        p = id_sync.signed_out_params(key)
        out_url = f"{base}/sync/{key}/out?ts={p['ts']}&sig={p['sig']}"
    except RuntimeError:
        out_url = None
    mappings = await pool.fetchval(
        "SELECT count(*) FROM id_sync_map WHERE partner_id = $1 AND expires_at > now()", row["id"]
    )
    return {
        "enabled_globally": id_sync.enabled(),
        "partner_sync_enabled": identity.sync_enabled,
        "in_url": f"{base}/sync/{key}/in?{identity.sync_in_param}={{SSP_UID}}&gdpr={{GDPR}}&gdpr_consent={{GDPR_CONSENT}}",
        "out_url": out_url,
        "optout_url": f"{base}/sync/optout",
        "active_mappings": int(mappings or 0),
        "secret_configured": out_url is not None,
    }


@router.delete("/{partner_id}", status_code=204)
async def delete_partner(partner_id: str, user: dict = Depends(require_admin)):
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM supply_partners WHERE id = $1 AND user_id = $2", partner_id, user["id"]
    )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")


# --- Auth-токен ----------------------------------------------------------------


@router.post("/{partner_id}/token")
async def rotate_token(partner_id: str, user: dict = Depends(require_admin)):
    """Генерирует (или перегенерирует) shared-secret токен партнёра.

    Полное значение токена возвращается ТОЛЬКО из этого ответа — сохраните
    и передайте SSP. Дальше API отдаёт только хвост (auth_token_hint).

    Ротация атомарна (один UPDATE) и безразрывна: прежний хэш переезжает в
    token_prev_hash и принимается ещё PREV_TOKEN_GRACE (15 мин) — SSP успевает
    переключить секрет без окна отказов (audit S-06). Если токена не было,
    prev не заводится.
    """
    pool = await get_pool()
    token = ssp_tokens.generate_token()
    grace_seconds = int(ssp_tokens.PREV_TOKEN_GRACE.total_seconds())
    row = await pool.fetchrow(
        """UPDATE supply_partners
              SET token_prev_hash = CASE WHEN auth_token_hash IS NOT NULL AND auth_token_hash <> ''
                                         THEN auth_token_hash END,
                  token_prev_valid_until = CASE WHEN auth_token_hash IS NOT NULL AND auth_token_hash <> ''
                                                THEN now() + make_interval(secs => $5) END,
                  auth_token_hash = $3, auth_token_hint = $4,
                  token_revoked_at = NULL, updated_at = now()
            WHERE id = $1 AND user_id = $2
            RETURNING id, token_prev_valid_until""",
        partner_id, user["id"], ssp_tokens.hash_token(token), ssp_tokens.token_hint(token), grace_seconds,
    )
    if not row:
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")
    prev_until = row["token_prev_valid_until"]
    return {
        "token": token,
        "header": ssp_tokens.HEADER_NAME,
        "prev_token_valid_until": prev_until.isoformat() if prev_until else None,
    }


@router.delete("/{partner_id}/token", status_code=204)
async def revoke_token(partner_id: str, user: dict = Depends(require_admin)):
    """Отзывает токен и СТАВИТ ПАРТНЁРА НА ПАУЗУ.

    Без токена биддер всё равно ответит 403 (fail-closed), но paused делает это
    явным в UI и убирает партнёра из фида Go — отзыв закрывает дверь, а не
    оставляет endpoint «без авторизации» (audit S-06). Окно ротации тоже
    закрывается: предыдущий токен более не принимается.
    """
    pool = await get_pool()
    result = await pool.execute(
        """UPDATE supply_partners
              SET auth_token_hash = NULL, auth_token_hint = NULL,
                  token_prev_hash = NULL, token_prev_valid_until = NULL,
                  token_revoked_at = now(),
                  status = CASE WHEN status IN ('testing', 'active') THEN 'paused' ELSE status END,
                  updated_at = now()
            WHERE id = $1 AND user_id = $2""",
        partner_id, user["id"],
    )
    if result.endswith("0"):
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")
    # Партнёр только что ушёл из фида — пусть Go перечитает, а не ждёт 30 с.
    await ssp_check.request_feed_reload()


# --- Автопроверка подключения ----------------------------------------------------


@router.post("/{partner_id}/check")
async def connection_check(partner_id: str, user: dict = Depends(require_admin)):
    """Прогоняет интеграционный чек-лист подключения и сохраняет результат.

    Проверка идёт через боевой Go-биддер по HTTP (ssp_check.go_bidder_caller),
    а не через Python-паритет: иначе результат ничего не говорит о том, что
    увидит SSP в продакшене. Партнёр в статусе draft переводится в testing
    (биддер и фид принимают active/testing) — это и есть смысл теста.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM supply_partners WHERE id = $1 AND user_id = $2", partner_id, user["id"]
    )
    if not row:
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")
    partner = dict(row)
    for k in ("allowed_ad_types", "ip_whitelist"):
        if isinstance(partner.get(k), str):
            partner[k] = json.loads(partner[k])

    # Без токена Go ответит 403 на каждый шаг — проверять нечего (S-06).
    _require_token_for_serving("testing", partner.get("auth_token_hash"))

    # draft -> testing: endpoint начинает принимать запросы
    if partner["status"] == "draft":
        await pool.execute(
            "UPDATE supply_partners SET status = 'testing', updated_at = now() WHERE id = $1", partner_id
        )
        partner["status"] = "testing"

    # Go перечитывает фид раз в 30 с: только что переведённый в testing партнёр
    # ещё не в снапшоте -> проверка дала бы 404. Просим перечитать сейчас.
    reload_ok, reload_detail = await ssp_check.request_feed_reload_detail()
    result = await ssp_check.run_connection_check(partner, ssp_check.go_bidder_caller())
    if not reload_ok:
        # Audit R-02: биддер честно сообщает, что рефреш не удался. Показываем
        # это первой строкой — иначе 404/503 ниже выглядят как ошибка партнёра.
        result["checks"].insert(0, {
            "key": "feed_reload", "label": "Обновление фида биддера", "ok": False,
            "detail": reload_detail or "рефреш не удался — биддер торгует по прошлому снапшоту",
        })
        result["passed"] = False
    await pool.execute(
        "UPDATE supply_partners SET last_check = $2::jsonb, updated_at = now() WHERE id = $1",
        partner_id, json.dumps(result),
    )
    return result


_PARTNER_WHERE = """
    e.supply_partner_id = $1::uuid
    AND e.created_at >= now() - make_interval(days => $2)
    AND NOT e.is_test
"""

_METRIC_COLS = f"""
    count(*) FILTER (WHERE e.event_type = 'bid')        AS bids,
    count(*) FILTER (WHERE e.event_type = 'win')        AS wins,
    count(*) FILTER (WHERE e.event_type = 'impression') AS impressions,
    count(*) FILTER (WHERE e.event_type = 'click')      AS clicks,
    count(*) FILTER (WHERE e.event_type = 'loss')       AS losses,
    {spend_expr("e")} AS spend
"""


def _metrics(r) -> dict:
    """Операционный отчёт по SSP: spend — net (себестоимость), наценка не
    применяется намеренно (смотрит владелец партнёра/админ). Производные —
    через stats_math.derive, как во всех отчётах (F-02)."""
    bids, wins, imps, clicks = int(r["bids"]), int(r["wins"]), int(r["impressions"]), int(r["clicks"])
    spend = money(r["spend"])
    d = derive(bids=bids, wins=wins, impressions=imps, clicks=clicks, spend=spend)
    return {
        "bids": bids,
        "wins": wins,
        "impressions": imps,
        "clicks": clicks,
        "losses": int(r["losses"]),
        "spend": spend,
        "win_rate": d["win_rate"],
        "ctr": d["ctr"],
    }


@router.get("/{partner_id}/stats")
async def partner_stats(
    partner_id: str,
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(current_user),
):
    """Карточка SSP: торговые показатели партнёра за окно `days` из bid_events.

    Всё — по нашим событиям (ставка/выигрыш/показ/клик/loss). Число входящих
    bid request'ов в БД не пишется (только счётчик bidder_partner_requests_total
    в Prometheus), поэтому fill-rate здесь не считается — см. Grafana.
    """
    from routers.stats import LOSS_REASON_LABELS

    pool = await get_pool()
    owner = await pool.fetchrow(
        "SELECT id FROM supply_partners WHERE id = $1 AND user_id = $2", partner_id, user["id"]
    )
    if not owner:
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")

    totals = await pool.fetchrow(
        f"""
        SELECT {_METRIC_COLS},
               avg(e.price)      FILTER (WHERE e.event_type = 'bid')  AS avg_bid,
               avg(e.bidfloor)   FILTER (WHERE e.event_type = 'bid')  AS avg_bidfloor,
               avg(e.min_to_win) FILTER (WHERE e.event_type = 'loss') AS avg_min_to_win
        FROM bid_events e WHERE {_PARTNER_WHERE}
        """,
        partner_id, days,
    )
    daily = await pool.fetch(
        f"""
        SELECT date_trunc('day', e.created_at) AS ts, {_METRIC_COLS}
        FROM bid_events e WHERE {_PARTNER_WHERE}
        GROUP BY 1 ORDER BY 1
        """,
        partner_id, days,
    )
    loss_rows = await pool.fetch(
        f"""
        SELECT COALESCE(e.loss_reason, '—') AS reason, count(*) AS cnt
        FROM bid_events e WHERE {_PARTNER_WHERE} AND e.event_type = 'loss'
        GROUP BY 1 ORDER BY 2 DESC LIMIT 12
        """,
        partner_id, days,
    )
    placements = await pool.fetch(
        f"""
        SELECT COALESCE(e.publisher_id, '—') AS publisher_id, COALESCE(e.tagid, '—') AS tagid,
               COALESCE(e.domain, e.bundle, '—') AS site, {_METRIC_COLS}
        FROM bid_events e WHERE {_PARTNER_WHERE}
        GROUP BY 1, 2, 3 ORDER BY bids DESC LIMIT 15
        """,
        partner_id, days,
    )
    ad_types = await pool.fetch(
        f"""
        SELECT COALESCE(e.ad_type, '—') AS ad_type, {_METRIC_COLS}
        FROM bid_events e WHERE {_PARTNER_WHERE}
        GROUP BY 1 ORDER BY bids DESC
        """,
        partner_id, days,
    )

    total_losses = sum(int(r["cnt"]) for r in loss_rows) or 0
    return {
        "days": days,
        "totals": {
            **_metrics(totals),
            "avg_bid": round(float(totals["avg_bid"]), 4) if totals["avg_bid"] is not None else None,
            "avg_bidfloor": round(float(totals["avg_bidfloor"]), 4) if totals["avg_bidfloor"] is not None else None,
            "avg_min_to_win": round(float(totals["avg_min_to_win"]), 4) if totals["avg_min_to_win"] is not None else None,
        },
        "daily": [{"ts": r["ts"].isoformat(), **_metrics(r)} for r in daily],
        "loss_reasons": [
            {
                "reason": r["reason"],
                "label": LOSS_REASON_LABELS.get(r["reason"], r["reason"]),
                "count": int(r["cnt"]),
                "share": pct(r["cnt"], total_losses) if total_losses else None,
            }
            for r in loss_rows
        ],
        "placements": [
            {"publisher_id": r["publisher_id"], "tagid": r["tagid"], "site": r["site"], **_metrics(r)}
            for r in placements
        ],
        "ad_types": [{"ad_type": r["ad_type"], **_metrics(r)} for r in ad_types],
    }


# ---------- GEO v2: алиасы значений SSP (docs/GEO.md) ----------


class GeoAliasIn(BaseModel):
    kind: Literal["region", "city"]
    ssp_value: str = Field(min_length=1, max_length=200)
    place_id: int = Field(gt=0)


async def _own_partner(pool, partner_id: str, user: dict) -> None:
    if not await pool.fetchval(
        "SELECT 1 FROM supply_partners WHERE id = $1 AND user_id = $2", partner_id, user["id"]
    ):
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")


def _alias_out(r) -> dict:
    return {
        "id": r["id"], "kind": r["kind"], "ssp_value": r["ssp_value"], "place_id": r["place_id"],
        "place_name": r["name_ru"], "place_type": r["type"], "updated_at": r["updated_at"].isoformat(),
    }


@router.get("/{partner_id}/geo-aliases")
async def list_geo_aliases(partner_id: str, user: dict = Depends(require_admin)):
    pool = await get_pool()
    await _own_partner(pool, partner_id, user)
    rows = await pool.fetch(
        """SELECT a.id, a.kind, a.ssp_value, a.place_id, a.updated_at, p.name_ru, p.type
             FROM geo_ssp_alias a JOIN geo_places p ON p.id = a.place_id
            WHERE a.partner_id = $1 ORDER BY a.kind, a.value_norm""",
        partner_id,
    )
    return [_alias_out(r) for r in rows]


@router.put("/{partner_id}/geo-aliases")
async def upsert_geo_alias(partner_id: str, body: GeoAliasIn, user: dict = Depends(require_admin)):
    """Значение SSP (как пришло в device.geo.region/city) -> место справочника.
    Повтор с тем же значением перезаписывает привязку (идемпотентно)."""
    value_norm = geo_normalize(body.ssp_value)
    if not value_norm:
        raise HTTPException(status_code=422, detail="ssp_value пустое после нормализации")
    pool = await get_pool()
    await _own_partner(pool, partner_id, user)
    place_type = await pool.fetchval("SELECT type FROM geo_places WHERE id = $1", body.place_id)
    expected = ("region",) if body.kind == "region" else ("city", "town")
    if place_type not in expected:
        raise HTTPException(status_code=422, detail=f"place_id {body.place_id}: ожидается {' / '.join(expected)}")
    row = await pool.fetchrow(
        """WITH up AS (
               INSERT INTO geo_ssp_alias (partner_id, kind, ssp_value, value_norm, place_id, created_by)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (partner_id, kind, value_norm)
               DO UPDATE SET ssp_value = EXCLUDED.ssp_value, place_id = EXCLUDED.place_id, updated_at = now()
               RETURNING id, kind, ssp_value, place_id, updated_at)
           SELECT up.*, p.name_ru, p.type FROM up JOIN geo_places p ON p.id = up.place_id""",
        partner_id, body.kind, body.ssp_value.strip(), value_norm, body.place_id, user["id"],
    )
    return _alias_out(row)


@router.delete("/{partner_id}/geo-aliases/{alias_id}", status_code=204)
async def delete_geo_alias(partner_id: str, alias_id: int, user: dict = Depends(require_admin)):
    pool = await get_pool()
    await _own_partner(pool, partner_id, user)
    deleted = await pool.fetchval(
        "DELETE FROM geo_ssp_alias WHERE id = $1 AND partner_id = $2 RETURNING id", alias_id, partner_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Алиас не найден")
