from fastapi import APIRouter, Depends, HTTPException

import conversion_attribution as attribution
from auth import current_user
from db import get_pool
from rbac import spend_multiplier, visible_user_ids
from stats_math import (
    ADDITIVE_METRICS, CONFIDENCE_LOW, NOT_SET_LABEL, OTHER_LABEL, OUT_OF_WINDOW_LABEL, ROW_KIND_DATA,
    ROW_KIND_NOT_SET, ROW_KIND_OTHER, ROW_KIND_OUT_OF_WINDOW, ROW_KIND_UNATTRIBUTED, ROW_KINDS_WITHOUT_CPA,
    UNATTRIBUTED_LABEL, DateRangeError, clamp_hours, clamp_limit, coverage_confidence, derive, money,
    billable_expr, parse_date_range, present_spend, spend_expr,
)

router = APIRouter(prefix="/stats", tags=["stats"])


async def _resolve_scope(user: dict) -> tuple[list[str], str | None, float]:
    """Return (visible_user_ids, advertiser_filter, spend_multiplier).

    - viewer (гость): данные владельца, опционально один рекламодатель; spend —
      по snapshot наценки на guest_access (зафиксирован при создании, F-02)
    - agency: свои данные + все потомки (клиенты)
    - client/agency: спенд показывается с наценкой по цепочке (markup_pct)
    - остальные: только свои данные, без наценки
    """
    if user.get("role") == "viewer":
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT owner_id, advertiser_id, markup_mult_snapshot, revoked_at FROM guest_access WHERE user_id = $1",
            user["id"],
        )
        if not row:
            raise HTTPException(status_code=403, detail="Гостевой доступ не настроен")
        if row["revoked_at"] is not None:
            raise HTTPException(status_code=403, detail="Доступ отозван")
        mult = float(row["markup_mult_snapshot"] or 1)
        return [str(row["owner_id"])], str(row["advertiser_id"]) if row["advertiser_id"] else None, mult

    scope = await visible_user_ids(user)
    uids = scope if scope is not None else [str(user["id"])]
    # can_see_all-роли в статистике видят только свой кабинет (как раньше)
    if scope is None:
        uids = [str(user["id"])]

    mult = await spend_multiplier(str(user["id"]), user.get("role", ""))
    return uids, None, mult


def _date_range_or_422(date_from, date_to):
    try:
        return parse_date_range(date_from, date_to)
    except DateRangeError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/summary")
async def summary(hours: int = 24, campaign_id: str | None = None, user: dict = Depends(current_user)):
    """Totals + deltas for the dashboard. Optionally scoped to one campaign."""
    uid, adv_scope, mult = await _resolve_scope(user)
    hours = clamp_hours(hours)
    pool = await get_pool()
    extra = []
    filters = ""
    if campaign_id:
        extra.append(campaign_id)
        filters += f" AND s.campaign_id = ${2 + len(extra)}"
    if adv_scope:
        extra.append(adv_scope)
        filters += f" AND c.advertiser_id = ${2 + len(extra)}"
    row = await pool.fetchrow(
        f"""
        SELECT
          COALESCE(SUM(s.bids), 0)        AS bids,
          COALESCE(SUM(s.wins), 0)        AS wins,
          COALESCE(SUM(s.impressions), 0) AS impressions,
          COALESCE(SUM(s.clicks), 0)      AS clicks,
          COALESCE(SUM(s.viewable_imps), 0) AS viewable_imps,
          COALESCE(SUM(s.billable_imps), 0) AS billable_imps,
          COALESCE(SUM(s.spend), 0)       AS spend
        FROM stats_hourly s
        JOIN campaigns c ON c.id = s.campaign_id
        WHERE c.user_id = ANY($1::uuid[]) AND s.ts_hour >= now() - ($2 || ' hours')::interval {filters}
        """,
        uid, str(hours), *extra,
    )
    counts = await pool.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM campaigns WHERE user_id = ANY($1::uuid[]) AND status = 'active')       AS active_campaigns,
          (SELECT count(*) FROM campaigns WHERE user_id = ANY($1::uuid[]))                             AS total_campaigns,
          (SELECT count(*) FROM creatives WHERE user_id = ANY($1::uuid[]))                             AS total_creatives,
          (SELECT count(*) FROM supply_partners WHERE user_id = ANY($1::uuid[]) AND status = 'active') AS active_partners
        """,
        uid,
    )
    bids, wins = int(row["bids"]), int(row["wins"])
    imps, clicks = int(row["impressions"]), int(row["clicks"])
    viewable = int(row["viewable_imps"])
    billable = int(row["billable_imps"])
    spend = float(row["spend"]) * mult
    return {
        "bids": bids,
        "wins": wins,
        "impressions": imps,
        "clicks": clicks,
        "viewable_imps": viewable,
        "billable_imps": billable,
        "spend": spend,
        **derive(bids=bids, wins=wins, impressions=imps, clicks=clicks, viewable_imps=viewable, spend=spend,
                 billable_imps=billable),
        **{k: int(v) for k, v in dict(counts).items()},
    }


_GRANULARITIES = {"hour": "hour", "day": "day", "week": "week"}


@router.get("/timeseries")
async def timeseries(
    hours: int = 24,
    campaign_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    granularity: str = "hour",
    user: dict = Depends(current_user),
):
    """Временной ряд для графика дашборда.

    Два режима периода:
    - hours (пресеты 24ч/3д/7д/30д) — по умолчанию;
    - date_from/date_to (YYYY-MM-DD, произвольный диапазон) — приоритетнее hours.
    granularity: hour | day | week — агрегация date_trunc на стороне БД.
    """
    trunc = _GRANULARITIES.get(granularity)
    if trunc is None:
        raise HTTPException(422, "granularity должен быть hour, day или week")

    uid, adv_scope, mult = await _resolve_scope(user)
    pool = await get_pool()

    period_filter = ""
    params: list = [uid]
    if date_from or date_to:
        d_from, d_to = _date_range_or_422(date_from, date_to)
        params.extend([d_from, d_to])
        period_filter = " AND s.ts_hour >= $2::date AND s.ts_hour < ($3::date + interval '1 day')"
    else:
        params.append(str(clamp_hours(hours)))
        period_filter = " AND s.ts_hour >= now() - ($2 || ' hours')::interval"

    filters = ""
    if campaign_id:
        params.append(campaign_id)
        filters += f" AND s.campaign_id = ${len(params)}"
    if adv_scope:
        params.append(adv_scope)
        filters += f" AND c.advertiser_id = ${len(params)}"

    rows = await pool.fetch(
        f"""
        SELECT date_trunc('{trunc}', s.ts_hour) AS bucket,
               SUM(s.bids) AS bids, SUM(s.wins) AS wins,
               SUM(s.impressions) AS impressions, SUM(s.clicks) AS clicks, SUM(s.spend) AS spend
        FROM stats_hourly s
        JOIN campaigns c ON c.id = s.campaign_id
        WHERE c.user_id = ANY($1::uuid[]) {period_filter} {filters}
        GROUP BY bucket ORDER BY bucket
        """,
        *params,
    )
    return [
        {
            "ts": r["bucket"].isoformat(),
            "bids": int(r["bids"]),
            "wins": int(r["wins"]),
            "impressions": int(r["impressions"]),
            "clicks": int(r["clicks"]),
            "spend": present_spend(r["spend"], mult),
        }
        for r in rows
    ]


_DIMENSIONS = {
    "campaign": (
        """
        SELECT c.id, c.name, c.status,
               COALESCE(SUM(s.bids), 0) AS bids, COALESCE(SUM(s.wins), 0) AS wins,
               COALESCE(SUM(s.impressions), 0) AS impressions,
               COALESCE(SUM(s.clicks), 0) AS clicks, COALESCE(SUM(s.spend), 0) AS spend,
               COALESCE(SUM(s.viewable_imps), 0) AS viewable_imps,
               COALESCE(SUM(s.billable_imps), 0) AS billable_imps
        FROM campaigns c
        LEFT JOIN stats_hourly s ON s.campaign_id = c.id AND s.ts_hour >= now() - ($2 || ' hours')::interval
        WHERE c.user_id = ANY($1::uuid[])
        GROUP BY c.id, c.name, c.status
        ORDER BY spend DESC
        """
    ),
    "partner": (
        """
        SELECT p.id, p.name, p.status,
               COALESCE(SUM(s.bids), 0) AS bids, COALESCE(SUM(s.wins), 0) AS wins,
               COALESCE(SUM(s.impressions), 0) AS impressions,
               COALESCE(SUM(s.clicks), 0) AS clicks, COALESCE(SUM(s.spend), 0) AS spend,
               COALESCE(SUM(s.viewable_imps), 0) AS viewable_imps,
               COALESCE(SUM(s.billable_imps), 0) AS billable_imps
        FROM supply_partners p
        LEFT JOIN stats_hourly s ON s.supply_partner_id = p.id AND s.ts_hour >= now() - ($2 || ' hours')::interval
        WHERE p.user_id = ANY($1::uuid[])
        GROUP BY p.id, p.name, p.status
        ORDER BY bids DESC
        """
    ),
    "creative": (
        """
        SELECT cr.id, cr.name, cr.status,
               COALESCE(SUM(s.bids), 0) AS bids, COALESCE(SUM(s.wins), 0) AS wins,
               COALESCE(SUM(s.impressions), 0) AS impressions,
               COALESCE(SUM(s.clicks), 0) AS clicks, COALESCE(SUM(s.spend), 0) AS spend,
               COALESCE(SUM(s.viewable_imps), 0) AS viewable_imps,
               COALESCE(SUM(s.billable_imps), 0) AS billable_imps
        FROM creatives cr
        LEFT JOIN stats_hourly s ON s.creative_id = cr.id AND s.ts_hour >= now() - ($2 || ' hours')::interval
        WHERE cr.user_id = ANY($1::uuid[])
        GROUP BY cr.id, cr.name, cr.status
        ORDER BY spend DESC
        """
    ),
}


@router.get("/domains")
async def domain_breakdown(hours: int = 168, limit: int = 100, user: dict = Depends(current_user)):
    """Domain-level stats for one-click blacklisting. Scoped to the user's campaigns."""
    uid, _adv, mult = await _resolve_scope(user)
    hours, limit = clamp_hours(hours), clamp_limit(limit, 100)
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT d.domain,
               sum(d.wins)          AS wins,
               sum(d.impressions)   AS impressions,
               sum(d.clicks)        AS clicks,
               sum(d.viewable_imps) AS viewable_imps,
               sum(d.spend)         AS spend
        FROM domain_stats_hourly d
        JOIN campaigns c ON c.id = d.campaign_id
        WHERE c.user_id = ANY($1::uuid[]) AND d.ts_hour >= now() - ($2 || ' hours')::interval
        GROUP BY d.domain
        ORDER BY sum(d.spend) DESC, sum(d.impressions) DESC
        LIMIT $3
        """,
        uid, str(hours), limit,
    )
    blocked = await pool.fetch(
        "SELECT domain FROM domain_lists WHERE user_id = $1 AND list_type = 'black'", user["id"]
    )
    blocked_set = {r["domain"] for r in blocked}
    out = []
    for r in rows:
        spend = present_spend(r["spend"], mult)
        d = derive(wins=r["wins"], impressions=r["impressions"], clicks=r["clicks"],
                   viewable_imps=r["viewable_imps"], spend=spend)
        out.append({
            "domain": r["domain"], "wins": int(r["wins"]), "impressions": int(r["impressions"]),
            "clicks": int(r["clicks"]), "spend": spend, "viewability": d["viewability"],
            "blacklisted": r["domain"] in blocked_set,
        })
    return out


@router.get("/domain-quality")
async def domain_quality_panel(limit: int = 100, user: dict = Depends(current_user)):
    """Панель качества доменов (Часть B): скоринг платформенного ML-слоя.

    Скоринг платформенный (по всему трафику), но список показываем только
    для доменов, где у пользователя была активность — без утечки чужих площадок.
    """
    from ml.quality import CLEAN_SCORE_THRESHOLD

    uid, _adv, _mult = await _resolve_scope(user)
    limit = clamp_limit(limit, 100)
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT dq.domain, dq.score, dq.imps, dq.clicks, dq.wins, dq.reasons, dq.updated_at
        FROM domain_quality dq
        WHERE EXISTS (
            SELECT 1 FROM domain_stats_hourly d
            JOIN campaigns c ON c.id = d.campaign_id
            WHERE c.user_id = ANY($1::uuid[]) AND lower(d.domain) = dq.domain
        )
        ORDER BY dq.score ASC, dq.imps DESC
        LIMIT $2
        """,
        uid, limit,
    )
    import json as _json
    out = []
    for r in rows:
        reasons = r["reasons"]
        if isinstance(reasons, str):
            reasons = _json.loads(reasons or "[]")
        out.append({
            "domain": r["domain"], "score": round(float(r["score"]), 3),
            "imps": int(r["imps"]), "clicks": int(r["clicks"]), "wins": int(r["wins"]),
            "reasons": reasons, "flagged": float(r["score"]) < CLEAN_SCORE_THRESHOLD,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        })
    return {"threshold": CLEAN_SCORE_THRESHOLD, "domains": out}


@router.get("/breakdown")
async def breakdown(hours: int = 168, dimension: str = "campaign", user: dict = Depends(current_user)):
    """Aggregated stats grouped by campaign, partner, or creative."""
    uid, adv_scope, mult = await _resolve_scope(user)
    if adv_scope and dimension != "campaign":
        # Guests scoped to one advertiser only get the campaign breakdown
        dimension = "campaign"
    sql = _DIMENSIONS.get(dimension, _DIMENSIONS["campaign"])
    if adv_scope:
        sql = sql.replace("WHERE c.user_id = ANY($1::uuid[])", "WHERE c.user_id = ANY($1::uuid[]) AND c.advertiser_id = $3")
    pool = await get_pool()
    args = [uid, str(clamp_hours(hours))] + ([adv_scope] if adv_scope else [])
    rows = await pool.fetch(sql, *args)
    out = []
    for r in rows:
        bids, wins = int(r["bids"]), int(r["wins"])
        imps, clicks = int(r["impressions"]), int(r["clicks"])
        viewable = int(r["viewable_imps"] or 0)
        billable = int(r["billable_imps"] or 0)
        spend = present_spend(r["spend"], mult)
        out.append({
            "id": str(r["id"]),
            "name": r["name"],
            "status": r["status"],
            "bids": bids, "wins": wins, "impressions": imps, "clicks": clicks,
            "viewable_imps": viewable, "billable_imps": billable,
            "spend": spend,
            **derive(bids=bids, wins=wins, impressions=imps, clicks=clicks, viewable_imps=viewable, spend=spend,
                     billable_imps=billable),
        })
    return out


# ---------------------------------------------------------------------------
# Report constructor: flexible groupings + filters (whitelisted, no SQL injection)
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field

# dimensions servable from stats_hourly (fast, pre-aggregated)
_DIM_HOURLY: dict[str, str] = {
    "hour": "date_trunc('hour', s.ts_hour)",
    "day": "date_trunc('day', s.ts_hour)",
    "week": "date_trunc('week', s.ts_hour)",
    "month": "date_trunc('month', s.ts_hour)",
    "campaign": "s.campaign_id",
    "creative": "s.creative_id",
    "supply_partner": "s.supply_partner_id",
}

# dimensions only available from raw bid_events
_DIM_EVENTS: dict[str, str] = {
    "hour": "date_trunc('hour', e.created_at)",
    "day": "date_trunc('day', e.created_at)",
    "week": "date_trunc('week', e.created_at)",
    "month": "date_trunc('month', e.created_at)",
    "campaign": "e.campaign_id",
    "creative": "e.creative_id",
    "supply_partner": "e.supply_partner_id",
    # NULL остаётся NULL: строка получает NOT_SET_LABEL и kind=not_set (F-02),
    # а не тире, неотличимое от настоящего значения.
    "country": "e.geo_country",
    "device": "e.device_type",
    "os": "e.os",
    "browser": "e.browser",
    "ad_type": "e.ad_type",
    "domain": "e.domain",
    # supply-side slices (Go bidder writes them on bid/loss, see docs/CONTRACTS.md)
    "tagid": "e.tagid",
    "publisher": "e.publisher_id",
    "bundle": "e.bundle",
    "loss_reason": "e.loss_reason",
}

_EVENTS_ONLY_DIMS = {"country", "device", "os", "browser", "ad_type", "domain", "tagid", "publisher", "bundle", "loss_reason"}
_TIME_DIMS = ("hour", "day", "week", "month")

# Конверсии в raw-отчёте считаются из conversion_attributions (B14c): все
# измерения бида скопированы в строку атрибуции, поэтому доступны os/browser/
# ad_type/tagid/publisher/bundle. Недоступно только loss_reason — это измерение
# воронки bid→loss, у конверсии его нет: отдаём None и
# meta.conversions_available=false, а не ноль.
_DIM_CONV: dict[str, str] = {
    "hour": "date_trunc('hour', ca.converted_at)",
    "day": "date_trunc('day', ca.converted_at)",
    "week": "date_trunc('week', ca.converted_at)",
    "month": "date_trunc('month', ca.converted_at)",
    **{dim: f"ca.{col}" for dim, col in attribution.DIM_COLUMNS.items() if dim in _DIM_EVENTS},
}

# OpenRTB 2.5 §5.25 Loss Reason Codes — что SSP сообщает в ${AUCTION_LOSS}.
# Ключи — строки: consumer пишет loss_reason как text без приведения.
LOSS_REASON_LABELS: dict[str, str] = {
    "0": "Выиграли",
    "1": "Внутренняя ошибка SSP",
    "2": "Показ не состоялся (timeout)",
    "3": "Некорректный bid response",
    "4": "Некорректный deal ID",
    "5": "Некорректный auction ID",
    "6": "Некорректный advertiser domain",
    "7": "Отсутствуют маркеры/макросы",
    "8": "Не соответствует политике",
    "100": "Ставка ниже bidfloor",
    "101": "Ставка ниже deal floor",
    "102": "Проиграли по цене",
    "103": "Проиграли PMP-сделке",
    "104": "Покупатель занял слот",
    "105": "Проиграли гарантированному размещению",
    "200": "Креатив отклонён фильтром",
    "201": "Креатив на модерации",
    "202": "Креатив не соответствует размеру",
    "203": "Креатив не проходит по домену",
    "204": "Креатив не проходит по категории",
    "205": "Креатив: недопустимый атрибут",
    "206": "Креатив: недопустимый формат/MIME",
    "207": "Креатив: недопустимая длительность",
    "208": "Креатив: некорректный adm/VAST",
    "209": "Креатив: нет erid",
}


class ReportRequest(BaseModel):
    date_from: str  # ISO date, inclusive
    date_to: str  # ISO date, inclusive
    groupings: list[str] = Field(default_factory=lambda: ["day"])
    campaign_ids: list[str] = Field(default_factory=list)
    group_id: str | None = None
    countries: list[str] = Field(default_factory=list)
    devices: list[str] = Field(default_factory=list)
    limit: int = 1000


@router.post("/report")
async def report(body: ReportRequest, user: dict = Depends(current_user)):
    """Flexible report: pick groupings + filters; returns rows with all base metrics.

    Source auto-selection: stats_hourly when possible (fast), bid_events when a
    raw-event dimension (country/device/os/browser/ad_type/domain) is requested.

    Конверсии (F-02/B14c): в строках — только attributed (last click, в окне).
    На уровне рекламодателя (без фильтра по кампаниям/группе) добавляются
    служебные строки «Вне окна атрибуции» / «Без атрибуции», чтобы
    sum(rows.conversions) == totals.conversions == все конверсии пикселей
    владельца; totals.cpa считается по attributed. meta.conversions.coverage[dim]
    — доля конверсий, попадающих в строку измерения; < 0.8 → confidence=low.
    """
    owner_ids, _adv_filter, mult = await _resolve_scope(user)

    groupings = [g for g in body.groupings if g][:4] or ["day"]
    use_events = any(g in _EVENTS_ONLY_DIMS for g in groupings) or bool(body.countries) or bool(body.devices)
    dim_map = _DIM_EVENTS if use_events else _DIM_HOURLY
    for g in groupings:
        if g not in dim_map:
            raise HTTPException(status_code=400, detail=f"Недопустимая группировка: {g}")

    d_from, d_to = _date_range_or_422(body.date_from, body.date_to)
    limit = clamp_limit(body.limit)
    pool = await get_pool()
    empty_meta = {"source": "events" if use_events else "hourly", "truncated": False, "limit": limit,
                  "conversions_available": True, "conversions": None}
    # Уровень рекламодателя = весь скоуп владельца без фильтров по кампаниям;
    # только здесь есть смысл показывать конверсии, у которых нет campaign_id.
    advertiser_level = not body.campaign_ids and not body.group_id and _adv_filter is None

    # --- resolve campaign scope (ownership + optional group filter) ---
    async with pool.acquire() as conn:
        if body.group_id:
            rows = await conn.fetch(
                "SELECT id FROM campaigns WHERE user_id = ANY($1::uuid[]) AND group_id = $2::uuid",
                owner_ids, body.group_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT id FROM campaigns WHERE user_id = ANY($1::uuid[])", owner_ids
            )
        allowed = {str(r["id"]) for r in rows}
        if body.campaign_ids:
            scope_ids = [c for c in body.campaign_ids if c in allowed]
            if not scope_ids:
                return {"rows": [], "totals": None, "meta": empty_meta}
        else:
            scope_ids = list(allowed)
        if not scope_ids:
            return {"rows": [], "totals": None, "meta": empty_meta}

        sel_dims = ", ".join(f"{dim_map[g]} AS dim_{i}" for i, g in enumerate(groupings))
        grp_by = ", ".join(f"dim_{i}" for i in range(len(groupings)))
        # Временные ряды читают хронологически; остальное — крупное сверху,
        # чтобы за LIMIT уходил хвост, а не случайные группы.
        order_by = grp_by if all(g in _TIME_DIMS for g in groupings) else f"spend DESC, impressions DESC, bids DESC, {grp_by}"

        params: list = [scope_ids, d_from, d_to]
        conv_available = True

        if use_events:
            extra = ""
            conv_extra = ""
            if body.countries:
                params.append(body.countries)
                extra += f" AND e.geo_country = ANY(${len(params)})"
                conv_extra += f" AND ca.geo_country = ANY(${len(params)})"
            if body.devices:
                params.append(body.devices)
                extra += f" AND e.device_type = ANY(${len(params)})"
                conv_extra += f" AND ca.device_type = ANY(${len(params)})"
            metrics_sql = f"""
                   count(*) FILTER (WHERE e.event_type = 'bid')        AS bids,
                   count(*) FILTER (WHERE e.event_type = 'win')        AS wins,
                   count(*) FILTER (WHERE e.event_type = 'impression') AS impressions,
                   count(*) FILTER (WHERE e.event_type = 'click')      AS clicks,
                   count(*) FILTER (WHERE e.event_type = 'viewable')   AS viewable_imps,
                   {billable_expr("e")} AS billable_imps,
                   {spend_expr("e")} AS spend,
                   NULL::bigint AS conversions,
                   count(*) FILTER (WHERE e.event_type = 'loss')       AS losses,
                   avg(e.price)      FILTER (WHERE e.event_type = 'bid')  AS avg_bid,
                   avg(e.bidfloor)   FILTER (WHERE e.event_type = 'bid')  AS avg_bidfloor,
                   avg(e.min_to_win) FILTER (WHERE e.event_type = 'loss') AS avg_min_to_win
            """
            from_where = f"""
            FROM bid_events e
            WHERE e.campaign_id = ANY($1::uuid[])
              AND e.created_at >= $2::date
              AND e.created_at < ($3::date + interval '1 day')
              AND NOT e.is_test
              {extra}
            """
            conv_available = all(g in _DIM_CONV for g in groupings)
            conv_from_where = f"""
            FROM conversion_attributions ca
            WHERE ca.campaign_id = ANY($1::uuid[])
              AND ca.attribution_status = '{attribution.STATUS_ATTRIBUTED}'
              AND ca.converted_at >= $2::date
              AND ca.converted_at < ($3::date + interval '1 day')
              AND NOT ca.is_test
              {conv_extra}
            """
        else:
            metrics_sql = """
                   COALESCE(sum(s.bids), 0)        AS bids,
                   COALESCE(sum(s.wins), 0)        AS wins,
                   COALESCE(sum(s.impressions), 0) AS impressions,
                   COALESCE(sum(s.clicks), 0)      AS clicks,
                   COALESCE(sum(s.viewable_imps), 0) AS viewable_imps,
                   COALESCE(sum(s.billable_imps), 0) AS billable_imps,
                   COALESCE(sum(s.spend), 0)       AS spend,
                   COALESCE(sum(s.conversions), 0) AS conversions,
                   NULL::bigint AS losses, NULL::numeric AS avg_bid,
                   NULL::numeric AS avg_bidfloor, NULL::numeric AS avg_min_to_win
            """
            from_where = """
            FROM stats_hourly s
            WHERE s.campaign_id = ANY($1::uuid[])
              AND s.ts_hour >= $2::date
              AND s.ts_hour < ($3::date + interval '1 day')
            """

        # limit+1: один лишний ряд говорит, что были ещё группы (truncated),
        # без второго GROUP BY-скана ради точного count.
        recs = await conn.fetch(
            f"SELECT {sel_dims}, {metrics_sql} {from_where} GROUP BY {grp_by} ORDER BY {order_by} LIMIT {limit + 1}",
            *params,
        )
        truncated = len(recs) > limit
        recs = recs[:limit]
        # totals — тем же фильтром, без GROUP BY и без LIMIT: итог не зависит от обрезки.
        tot_rec = await conn.fetchrow(f"SELECT {metrics_sql} {from_where}", *params)

        conv_by_key: dict[tuple, int] = {}
        conv_total = None
        if use_events and conv_available:
            conv_dims = ", ".join(f"{_DIM_CONV[g]} AS dim_{i}" for i, g in enumerate(groupings))
            conv_rows = await conn.fetch(
                f"SELECT {conv_dims}, count(*) AS conversions {conv_from_where} GROUP BY {grp_by}", *params
            )
            conv_by_key = {
                tuple(_dim_key(r[f"dim_{i}"]) for i in range(len(groupings))): int(r["conversions"]) for r in conv_rows
            }
            conv_total = int(await conn.fetchval(f"SELECT count(*) {conv_from_where}", *params))

        # name lookups for id dimensions
        name_maps: dict[str, dict[str, str]] = {}
        for kind, table in (("campaign", "campaigns"), ("creative", "creatives"), ("supply_partner", "supply_partners")):
            if kind in groupings:
                nrows = await conn.fetch(f"SELECT id, name FROM {table}")
                name_maps[kind] = {str(r["id"]): r["name"] for r in nrows}
        if "loss_reason" in groupings:
            name_maps["loss_reason"] = LOSS_REASON_LABELS

        # Покрытие атрибуции владельца за период: coverage по запрошенным измерениям
        # (+ campaign = доля attributed вообще) и счётчики для служебных строк.
        cov_dims = list(dict.fromkeys(["campaign", *groupings]))
        attr = await attribution.owner_breakdown(conn, owner_ids, d_from, d_to, cov_dims)

    def metrics_of(r, conversions) -> dict:
        spend = present_spend(r["spend"], mult)
        m = {
            "bids": int(r["bids"] or 0), "wins": int(r["wins"] or 0),
            "impressions": int(r["impressions"] or 0), "clicks": int(r["clicks"] or 0),
            "viewable_imps": int(r["viewable_imps"] or 0),
            "billable_imps": int(r["billable_imps"] or 0),
            "spend": spend, "conversions": conversions,
            # hourly-агрегат проигрыши не измеряет: None, а не ноль
            "losses": int(r["losses"]) if r["losses"] is not None else None,
            # цены аукциона — деньги, показываем в той же цене, что и spend
            "avg_bid": money(float(r["avg_bid"]) * mult) if r["avg_bid"] is not None else None,
            "avg_bidfloor": money(float(r["avg_bidfloor"]) * mult) if r["avg_bidfloor"] is not None else None,
            "avg_min_to_win": money(float(r["avg_min_to_win"]) * mult) if r["avg_min_to_win"] is not None else None,
        }
        m.update(derive(bids=m["bids"], wins=m["wins"], impressions=m["impressions"], clicks=m["clicks"],
                        viewable_imps=m["viewable_imps"], spend=spend, conversions=conversions, losses=m["losses"],
                        billable_imps=m["billable_imps"]))
        return m

    def conversions_of(r) -> int | None:
        if not use_events:
            return int(r["conversions"] or 0)
        if not conv_available:
            return None
        return conv_by_key.get(tuple(_dim_key(r[f"dim_{i}"]) for i in range(len(groupings))), 0)

    out_rows = []
    for r in recs:
        row: dict = {"kind": ROW_KIND_DATA}
        for i, g in enumerate(groupings):
            v = r[f"dim_{i}"]
            if v is None:
                row[g] = NOT_SET_LABEL
                row["kind"] = ROW_KIND_NOT_SET
            elif g in name_maps:
                row[g] = name_maps[g].get(str(v), str(v))
                row[f"{g}_id"] = str(v)
            elif hasattr(v, "isoformat"):
                row[g] = v.isoformat()
            else:
                row[g] = str(v)
        row.update(metrics_of(r, conversions_of(r)))
        if row["kind"] in ROW_KINDS_WITHOUT_CPA:
            row["cpa"] = None
        out_rows.append(row)

    totals = metrics_of(tot_rec, (conv_total if conv_available else None) if use_events else int(tot_rec["conversions"] or 0))

    if truncated and out_rows:
        # «Прочее» = totals − показанные строки по аддитивным метрикам, чтобы
        # sum(rows) == totals; средние цены для свёртки не определены.
        other = {k: (totals[k] - sum((row[k] or 0) for row in out_rows)) if totals[k] is not None else None
                 for k in ADDITIVE_METRICS}
        other["spend"] = money(other["spend"])
        other_row: dict = {"kind": ROW_KIND_OTHER, groupings[0]: OTHER_LABEL}
        for g in groupings[1:]:
            other_row[g] = ""
        other_row.update(other)
        other_row.update({"avg_bid": None, "avg_bidfloor": None, "avg_min_to_win": None})
        other_row.update(derive(bids=other["bids"], wins=other["wins"], impressions=other["impressions"],
                                clicks=other["clicks"], viewable_imps=other["viewable_imps"], spend=other["spend"],
                                conversions=other["conversions"], losses=other["losses"],
                                billable_imps=other["billable_imps"]))
        out_rows.append(other_row)

    # Служебные строки уровня рекламодателя: конверсии без campaign_id. В totals
    # они входят (sum(rows) == totals), CPA по ним не определён; totals.cpa — по attributed.
    totals["conversions_attributed"] = totals["conversions"]
    if advertiser_level and conv_available and totals["conversions"] is not None:
        for kind, label, count in (
            (ROW_KIND_OUT_OF_WINDOW, OUT_OF_WINDOW_LABEL, attr["out_of_window"]),
            (ROW_KIND_UNATTRIBUTED, UNATTRIBUTED_LABEL, attr["unattributed"]),
        ):
            if count <= 0:
                continue
            out_rows.append(_service_conversion_row(kind, label, groupings, count))
            totals["conversions"] += count

    coverage = attr["coverage"]
    confidences = [coverage_confidence(v) for v in coverage.values()]
    meta = {
        "source": "events" if use_events else "hourly",
        "truncated": truncated,
        "limit": limit,
        "conversions_available": conv_available,
        "conversions": {
            "attributed": attr["attributed"],
            "out_of_window": attr["out_of_window"],
            "unattributed": attr["unattributed"],
            "coverage": coverage,
            "confidence": CONFIDENCE_LOW if CONFIDENCE_LOW in confidences else coverage_confidence(coverage.get("campaign")),
            "scope": "advertiser" if advertiser_level else "campaign",
        },
    }
    return {"rows": out_rows, "totals": totals, "meta": meta}


def _service_conversion_row(kind: str, label: str, groupings: list[str], conversions: int) -> dict:
    """Строка «Вне окна» / «Без атрибуции»: только конверсии, остальные метрики пустые."""
    row: dict = {"kind": kind, groupings[0]: label}
    for g in groupings[1:]:
        row[g] = ""
    row.update({k: 0 for k in ADDITIVE_METRICS})
    row.update({"spend": 0.0, "conversions": conversions, "losses": None,
                "avg_bid": None, "avg_bidfloor": None, "avg_min_to_win": None})
    row.update(derive(conversions=conversions, losses=None))
    row["cpa"] = None
    return row


def _dim_key(v):
    """Ключ для сопоставления строк отчёта и конверсий: uuid/datetime/str → hashable one way."""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)
