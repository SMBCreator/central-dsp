from fastapi import APIRouter, Depends, HTTPException

from auth import current_user
from db import get_pool
from rbac import effective_markup_pct, visible_user_ids

router = APIRouter(prefix="/stats", tags=["stats"])


async def _resolve_scope(user: dict) -> tuple[list[str], str | None, float]:
    """Return (visible_user_ids, advertiser_filter, spend_multiplier).

    - viewer (гость): данные владельца, опционально один рекламодатель
    - agency: свои данные + все потомки (клиенты)
    - client/agency: спенд показывается с наценкой по цепочке (markup_pct)
    - остальные: только свои данные, без наценки
    """
    if user.get("role") == "viewer":
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT owner_id, advertiser_id FROM guest_access WHERE user_id = $1", user["id"]
        )
        if not row:
            raise HTTPException(status_code=403, detail="Гостевой доступ не настроен")
        return [str(row["owner_id"])], str(row["advertiser_id"]) if row["advertiser_id"] else None, 1.0

    scope = await visible_user_ids(user)
    uids = scope if scope is not None else [str(user["id"])]
    # can_see_all-роли в статистике видят только свой кабинет (как раньше)
    if scope is None:
        uids = [str(user["id"])]

    mult = 1.0
    if user.get("role") in ("agency", "client"):
        mult = 1.0 + (await effective_markup_pct(str(user["id"]))) / 100.0
    return uids, None, mult


@router.get("/summary")
async def summary(hours: int = 24, campaign_id: str | None = None, user: dict = Depends(current_user)):
    """Totals + deltas for the dashboard. Optionally scoped to one campaign."""
    uid, adv_scope, mult = await _resolve_scope(user)
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
    spend = float(row["spend"]) * mult
    return {
        "bids": bids,
        "wins": wins,
        "impressions": imps,
        "clicks": clicks,
        "viewable_imps": viewable,
        "spend": round(spend, 4),
        "win_rate": round(wins / bids * 100, 2) if bids else 0,
        "ctr": round(clicks / imps * 100, 3) if imps else 0,
        "ecpm": round(spend / imps * 1000, 2) if imps else 0,
        "viewability": round(viewable / imps * 100, 1) if imps else 0,
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
    from datetime import date as _date

    trunc = _GRANULARITIES.get(granularity)
    if trunc is None:
        raise HTTPException(422, "granularity должен быть hour, day или week")

    uid, adv_scope, mult = await _resolve_scope(user)
    pool = await get_pool()

    # Валидация дат: строго ISO YYYY-MM-DD, from <= to, диапазон <= 366 дней
    period_filter = ""
    params: list = [uid]
    if date_from or date_to:
        if not (date_from and date_to):
            raise HTTPException(422, "Нужны обе даты: date_from и date_to")
        try:
            d_from, d_to = _date.fromisoformat(date_from), _date.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(422, "Даты в формате YYYY-MM-DD")
        if d_from > d_to:
            raise HTTPException(422, "date_from позже date_to")
        if (d_to - d_from).days > 366:
            raise HTTPException(422, "Диапазон не больше года")
        params.extend([d_from, d_to])
        period_filter = " AND s.ts_hour >= $2::date AND s.ts_hour < ($3::date + interval '1 day')"
    else:
        params.append(str(max(1, min(hours, 8784))))
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
            "spend": round(float(r["spend"]) * mult, 4),
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
               COALESCE(SUM(s.viewable_imps), 0) AS viewable_imps
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
               COALESCE(SUM(s.viewable_imps), 0) AS viewable_imps
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
               COALESCE(SUM(s.viewable_imps), 0) AS viewable_imps
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
    return [
        {"domain": r["domain"], "wins": int(r["wins"]), "impressions": int(r["impressions"]),
         "clicks": int(r["clicks"]), "spend": round(float(r["spend"]) * mult, 4),
         "viewability": round(int(r["viewable_imps"] or 0) / int(r["impressions"]) * 100, 1) if int(r["impressions"]) else 0,
         "blacklisted": r["domain"] in blocked_set}
        for r in rows
    ]


@router.get("/domain-quality")
async def domain_quality_panel(limit: int = 100, user: dict = Depends(current_user)):
    """Панель качества доменов (Часть B): скоринг платформенного ML-слоя.

    Скоринг платформенный (по всему трафику), но список показываем только
    для доменов, где у пользователя была активность — без утечки чужих площадок.
    """
    from ml.quality import CLEAN_SCORE_THRESHOLD

    uid, _adv, _mult = await _resolve_scope(user)
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
    args = [uid, str(hours)] + ([adv_scope] if adv_scope else [])
    rows = await pool.fetch(sql, *args)
    out = []
    for r in rows:
        bids, wins = int(r["bids"]), int(r["wins"])
        imps, clicks = int(r["impressions"]), int(r["clicks"])
        viewable = int(r["viewable_imps"] or 0)
        spend = float(r["spend"])
        out.append({
            "id": str(r["id"]),
            "name": r["name"],
            "status": r["status"],
            "bids": bids, "wins": wins, "impressions": imps, "clicks": clicks,
            "viewable_imps": viewable,
            "spend": round(spend, 4),
            "win_rate": round(wins / bids * 100, 2) if bids else 0,
            "ctr": round(clicks / imps * 100, 3) if imps else 0,
            "ecpm": round(spend / imps * 1000, 2) if imps else 0,
            "viewability": round(viewable / imps * 100, 1) if imps else 0,
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
    "country": "COALESCE(e.geo_country, '—')",
    "device": "COALESCE(e.device_type, '—')",
    "os": "COALESCE(e.os, '—')",
    "browser": "COALESCE(e.browser, '—')",
    "ad_type": "COALESCE(e.ad_type, '—')",
    "domain": "COALESCE(e.domain, '—')",
}

_EVENTS_ONLY_DIMS = {"country", "device", "os", "browser", "ad_type", "domain"}


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
    """
    owner_ids, adv_filter, _fx = await _resolve_scope(user)

    groupings = [g for g in body.groupings if g][:4] or ["day"]
    use_events = any(g in _EVENTS_ONLY_DIMS for g in groupings) or bool(body.countries) or bool(body.devices)
    dim_map = _DIM_EVENTS if use_events else _DIM_HOURLY
    for g in groupings:
        if g not in dim_map:
            raise HTTPException(status_code=400, detail=f"Недопустимая группировка: {g}")

    from datetime import date as _date

    try:
        d_from = _date.fromisoformat(body.date_from)
        d_to = _date.fromisoformat(body.date_to)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты (ожидается YYYY-MM-DD)")
    if d_from > d_to:
        d_from, d_to = d_to, d_from

    limit = max(1, min(body.limit, 5000))
    pool = await get_pool()

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
                return {"rows": [], "totals": None}
        else:
            scope_ids = list(allowed)
        if not scope_ids:
            return {"rows": [], "totals": None}

        sel_dims = ", ".join(f"{dim_map[g]} AS dim_{i}" for i, g in enumerate(groupings))
        grp_by = ", ".join(f"dim_{i}" for i in range(len(groupings)))

        params: list = [scope_ids, d_from, d_to]

        if use_events:
            extra = ""
            if body.countries:
                params.append(body.countries)
                extra += f" AND e.geo_country = ANY(${len(params)})"
            if body.devices:
                params.append(body.devices)
                extra += f" AND e.device_type = ANY(${len(params)})"
            q = f"""
            SELECT {sel_dims},
                   count(*) FILTER (WHERE e.event_type = 'bid')        AS bids,
                   count(*) FILTER (WHERE e.event_type = 'win')        AS wins,
                   count(*) FILTER (WHERE e.event_type = 'impression') AS impressions,
                   count(*) FILTER (WHERE e.event_type = 'click')      AS clicks,
                   count(*) FILTER (WHERE e.event_type = 'viewable')   AS viewable_imps,
                   COALESCE(sum(e.clear_price) FILTER (WHERE e.event_type = 'impression'), 0) / 1000.0 AS spend,
                   0::bigint AS conversions
            FROM bid_events e
            WHERE e.campaign_id = ANY($1::uuid[])
              AND e.created_at >= $2::date
              AND e.created_at < ($3::date + interval '1 day')
              AND NOT e.is_test
              {extra}
            GROUP BY {grp_by}
            ORDER BY {grp_by}
            LIMIT {limit}
            """
        else:
            q = f"""
            SELECT {sel_dims},
                   COALESCE(sum(s.bids), 0)        AS bids,
                   COALESCE(sum(s.wins), 0)        AS wins,
                   COALESCE(sum(s.impressions), 0) AS impressions,
                   COALESCE(sum(s.clicks), 0)      AS clicks,
                   COALESCE(sum(s.viewable_imps), 0) AS viewable_imps,
                   COALESCE(sum(s.spend), 0)       AS spend,
                   COALESCE(sum(s.conversions), 0) AS conversions
            FROM stats_hourly s
            WHERE s.campaign_id = ANY($1::uuid[])
              AND s.ts_hour >= $2::date
              AND s.ts_hour < ($3::date + interval '1 day')
            GROUP BY {grp_by}
            ORDER BY {grp_by}
            LIMIT {limit}
            """
        recs = await conn.fetch(q, *params)

        # name lookups for id dimensions
        name_maps: dict[str, dict[str, str]] = {}
        for kind, table in (("campaign", "campaigns"), ("creative", "creatives"), ("supply_partner", "supply_partners")):
            if kind in groupings:
                nrows = await conn.fetch(f"SELECT id, name FROM {table}")
                name_maps[kind] = {str(r["id"]): r["name"] for r in nrows}

    out_rows = []
    tot = {"bids": 0, "wins": 0, "impressions": 0, "clicks": 0, "viewable_imps": 0, "spend": 0.0, "conversions": 0}
    for r in recs:
        row: dict = {}
        for i, g in enumerate(groupings):
            v = r[f"dim_{i}"]
            if v is not None and g in name_maps:
                row[g] = name_maps[g].get(str(v), str(v))
                row[f"{g}_id"] = str(v)
            elif hasattr(v, "isoformat"):
                row[g] = v.isoformat()
            else:
                row[g] = str(v) if v is not None else "—"
        m = {
            "bids": int(r["bids"]), "wins": int(r["wins"]),
            "impressions": int(r["impressions"]), "clicks": int(r["clicks"]),
            "viewable_imps": int(r["viewable_imps"] or 0),
            "spend": float(r["spend"]), "conversions": int(r["conversions"]),
        }
        m["win_rate"] = round(m["wins"] / m["bids"] * 100, 2) if m["bids"] else 0.0
        m["ctr"] = round(m["clicks"] / m["impressions"] * 100, 3) if m["impressions"] else 0.0
        m["cpm"] = round(m["spend"] / m["impressions"] * 1000, 4) if m["impressions"] else 0.0
        m["cpc"] = round(m["spend"] / m["clicks"], 4) if m["clicks"] else 0.0
        m["cpa"] = round(m["spend"] / m["conversions"], 4) if m["conversions"] else 0.0
        m["viewability"] = round(m["viewable_imps"] / m["impressions"] * 100, 1) if m["impressions"] else 0.0
        row.update(m)
        out_rows.append(row)
        for k in tot:
            tot[k] += m[k]

    totals = dict(tot)
    totals["win_rate"] = round(totals["wins"] / totals["bids"] * 100, 2) if totals["bids"] else 0.0
    totals["ctr"] = round(totals["clicks"] / totals["impressions"] * 100, 3) if totals["impressions"] else 0.0
    totals["cpm"] = round(totals["spend"] / totals["impressions"] * 1000, 4) if totals["impressions"] else 0.0
    totals["cpc"] = round(totals["spend"] / totals["clicks"], 4) if totals["clicks"] else 0.0
    totals["cpa"] = round(totals["spend"] / totals["conversions"], 4) if totals["conversions"] else 0.0
    totals["viewability"] = round(totals["viewable_imps"] / totals["impressions"] * 100, 1) if totals["impressions"] else 0.0
    totals["spend"] = round(totals["spend"], 4)
    return {"rows": out_rows, "totals": totals}
