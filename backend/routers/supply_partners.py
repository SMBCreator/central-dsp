"""SSP-партнёры: онбординг, управление токеном и автопроверка подключения.

Создание/изменение/удаление — только superadmin/admin (require_admin):
подключение SSP — платформенная операция уровня администратора.
Чтение — любой платформенный пользователь (страница /supply).
"""

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import current_user
from rbac import require_admin
from db import get_pool
from schemas import SupplyPartnerIn, SupplyPartnerPatch
from stats_math import spend_expr
import ssp_check
import ssp_tokens

router = APIRouter(prefix="/supply-partners", tags=["supply-partners"])

_JSONB_FIELDS = {"allowed_ad_types", "ip_whitelist"}


def _serialize(row) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    d["user_id"] = str(d["user_id"])
    d["bid_floor"] = float(d["bid_floor"])
    for k in ("allowed_ad_types", "ip_whitelist", "last_check"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    # В БД только хэш (миграция 032); наружу — признак и хвост для сверки
    token_hash = d.pop("auth_token_hash", None)
    hint = d.pop("auth_token_hint", None)
    d.pop("auth_token", None)  # на случай прогона до миграции 032
    d.pop("token_prev_hash", None)  # хэш предыдущего токена наружу не нужен
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
    row = await pool.fetchrow(
        """
        INSERT INTO supply_partners
            (user_id, name, endpoint_key, auction_type, bid_floor, qps_limit, allowed_ad_types, notes,
             protocol_version, currency, tmax_ms, ip_whitelist, seat_id, contact_email, status)
        VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12::jsonb,$13,$14,'draft') RETURNING *
        """,
        user["id"], body.name, endpoint_key, body.auction_type, body.bid_floor,
        body.qps_limit, json.dumps(body.allowed_ad_types), body.notes,
        body.protocol_version, body.currency, body.tmax_ms,
        json.dumps(whitelist), body.seat_id, body.contact_email,
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
    row = await pool.fetchrow(
        f"UPDATE supply_partners SET {', '.join(sets)}, updated_at = now() WHERE id = $1 AND user_id = $2 RETURNING *",
        partner_id, user["id"], *values,
    )
    if not row:
        raise HTTPException(status_code=404, detail="SSP-партнёр не найден")
    return _serialize(row)


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
    await ssp_check.request_feed_reload()
    result = await ssp_check.run_connection_check(partner, ssp_check.go_bidder_caller())
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


def _ratio(num, den) -> float | None:
    return round(float(num) / float(den) * 100, 2) if den else None


def _metrics(r) -> dict:
    bids, wins, imps, clicks = int(r["bids"]), int(r["wins"]), int(r["impressions"]), int(r["clicks"])
    return {
        "bids": bids,
        "wins": wins,
        "impressions": imps,
        "clicks": clicks,
        "losses": int(r["losses"]),
        "spend": round(float(r["spend"]), 2),
        "win_rate": _ratio(wins, bids),
        "ctr": _ratio(clicks, imps),
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
                "share": _ratio(r["cnt"], total_losses),
            }
            for r in loss_rows
        ],
        "placements": [
            {"publisher_id": r["publisher_id"], "tagid": r["tagid"], "site": r["site"], **_metrics(r)}
            for r in placements
        ],
        "ad_types": [{"ad_type": r["ad_type"], **_metrics(r)} for r in ad_types],
    }
