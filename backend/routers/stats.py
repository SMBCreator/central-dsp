from fastapi import APIRouter, Depends, HTTPException

from auth import current_user
from db import get_pool

router = APIRouter(prefix="/stats", tags=["stats"])


async def _resolve_scope(user: dict) -> tuple[str, str | None]:
    """Return (effective_user_id, advertiser_filter).

    Viewer (guest) accounts see their owner's data, optionally scoped to one advertiser.
    """
    if user.get("role") != "viewer":
        return user["id"], None
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT owner_id, advertiser_id FROM guest_access WHERE user_id = $1", user["id"]
    )
    if not row:
        raise HTTPException(status_code=403, detail="Гостевой доступ не настроен")
    return str(row["owner_id"]), str(row["advertiser_id"]) if row["advertiser_id"] else None


@router.get("/summary")
async def summary(hours: int = 24, campaign_id: str | None = None, user: dict = Depends(current_user)):
    """Totals + deltas for the dashboard. Optionally scoped to one campaign."""
    uid, adv_scope = await _resolve_scope(user)
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
          COALESCE(SUM(s.spend), 0)       AS spend
        FROM stats_hourly s
        JOIN campaigns c ON c.id = s.campaign_id
        WHERE c.user_id = $1 AND s.ts_hour >= now() - ($2 || ' hours')::interval {filters}
        """,
        uid, str(hours), *extra,
    )
    counts = await pool.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM campaigns WHERE user_id = $1 AND status = 'active')       AS active_campaigns,
          (SELECT count(*) FROM campaigns WHERE user_id = $1)                             AS total_campaigns,
          (SELECT count(*) FROM creatives WHERE user_id = $1)                             AS total_creatives,
          (SELECT count(*) FROM supply_partners WHERE user_id = $1 AND status = 'active') AS active_partners
        """,
        uid,
    )
    bids, wins = int(row["bids"]), int(row["wins"])
    imps, clicks = int(row["impressions"]), int(row["clicks"])
    spend = float(row["spend"])
    return {
        "bids": bids,
        "wins": wins,
        "impressions": imps,
        "clicks": clicks,
        "spend": round(spend, 4),
        "win_rate": round(wins / bids * 100, 2) if bids else 0,
        "ctr": round(clicks / imps * 100, 3) if imps else 0,
        "ecpm": round(spend / imps * 1000, 2) if imps else 0,
        **{k: int(v) for k, v in dict(counts).items()},
    }


@router.get("/timeseries")
async def timeseries(hours: int = 24, campaign_id: str | None = None, user: dict = Depends(current_user)):
    uid, adv_scope = await _resolve_scope(user)
    pool = await get_pool()
    extra = []
    filters = ""
    if campaign_id:
        extra.append(campaign_id)
        filters += f" AND s.campaign_id = ${2 + len(extra)}"
    if adv_scope:
        extra.append(adv_scope)
        filters += f" AND c.advertiser_id = ${2 + len(extra)}"
    rows = await pool.fetch(
        f"""
        SELECT s.ts_hour,
               SUM(s.bids) AS bids, SUM(s.wins) AS wins,
               SUM(s.impressions) AS impressions, SUM(s.clicks) AS clicks, SUM(s.spend) AS spend
        FROM stats_hourly s
        JOIN campaigns c ON c.id = s.campaign_id
        WHERE c.user_id = $1 AND s.ts_hour >= now() - ($2 || ' hours')::interval {filters}
        GROUP BY s.ts_hour ORDER BY s.ts_hour
        """,
        uid, str(hours), *extra,
    )
    return [
        {
            "ts": r["ts_hour"].isoformat(),
            "bids": int(r["bids"]),
            "wins": int(r["wins"]),
            "impressions": int(r["impressions"]),
            "clicks": int(r["clicks"]),
            "spend": round(float(r["spend"]), 4),
        }
        for r in rows
    ]


_DIMENSIONS = {
    "campaign": (
        """
        SELECT c.id, c.name, c.status,
               COALESCE(SUM(s.bids), 0) AS bids, COALESCE(SUM(s.wins), 0) AS wins,
               COALESCE(SUM(s.impressions), 0) AS impressions,
               COALESCE(SUM(s.clicks), 0) AS clicks, COALESCE(SUM(s.spend), 0) AS spend
        FROM campaigns c
        LEFT JOIN stats_hourly s ON s.campaign_id = c.id AND s.ts_hour >= now() - ($2 || ' hours')::interval
        WHERE c.user_id = $1
        GROUP BY c.id, c.name, c.status
        ORDER BY spend DESC
        """
    ),
    "partner": (
        """
        SELECT p.id, p.name, p.status,
               COALESCE(SUM(s.bids), 0) AS bids, COALESCE(SUM(s.wins), 0) AS wins,
               COALESCE(SUM(s.impressions), 0) AS impressions,
               COALESCE(SUM(s.clicks), 0) AS clicks, COALESCE(SUM(s.spend), 0) AS spend
        FROM supply_partners p
        LEFT JOIN stats_hourly s ON s.supply_partner_id = p.id AND s.ts_hour >= now() - ($2 || ' hours')::interval
        WHERE p.user_id = $1
        GROUP BY p.id, p.name, p.status
        ORDER BY bids DESC
        """
    ),
    "creative": (
        """
        SELECT cr.id, cr.name, cr.status,
               COALESCE(SUM(s.bids), 0) AS bids, COALESCE(SUM(s.wins), 0) AS wins,
               COALESCE(SUM(s.impressions), 0) AS impressions,
               COALESCE(SUM(s.clicks), 0) AS clicks, COALESCE(SUM(s.spend), 0) AS spend
        FROM creatives cr
        LEFT JOIN stats_hourly s ON s.creative_id = cr.id AND s.ts_hour >= now() - ($2 || ' hours')::interval
        WHERE cr.user_id = $1
        GROUP BY cr.id, cr.name, cr.status
        ORDER BY spend DESC
        """
    ),
}


@router.get("/domains")
async def domain_breakdown(hours: int = 168, limit: int = 100, user: dict = Depends(current_user)):
    """Domain-level stats for one-click blacklisting. Scoped to the user's campaigns."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT d.domain,
               sum(d.wins)        AS wins,
               sum(d.impressions) AS impressions,
               sum(d.clicks)      AS clicks,
               sum(d.spend)       AS spend
        FROM domain_stats_hourly d
        JOIN campaigns c ON c.id = d.campaign_id
        WHERE c.user_id = $1 AND d.ts_hour >= now() - ($2 || ' hours')::interval
        GROUP BY d.domain
        ORDER BY sum(d.spend) DESC, sum(d.impressions) DESC
        LIMIT $3
        """,
        user["id"], str(hours), limit,
    )
    blocked = await pool.fetch(
        "SELECT domain FROM domain_lists WHERE user_id = $1 AND list_type = 'black'", user["id"]
    )
    blocked_set = {r["domain"] for r in blocked}
    return [
        {"domain": r["domain"], "wins": int(r["wins"]), "impressions": int(r["impressions"]),
         "clicks": int(r["clicks"]), "spend": float(r["spend"]),
         "blacklisted": r["domain"] in blocked_set}
        for r in rows
    ]


@router.get("/breakdown")
async def breakdown(hours: int = 168, dimension: str = "campaign", user: dict = Depends(current_user)):
    """Aggregated stats grouped by campaign, partner, or creative."""
    uid, adv_scope = await _resolve_scope(user)
    if adv_scope and dimension != "campaign":
        # Guests scoped to one advertiser only get the campaign breakdown
        dimension = "campaign"
    sql = _DIMENSIONS.get(dimension, _DIMENSIONS["campaign"])
    if adv_scope:
        sql = sql.replace("WHERE c.user_id = $1", "WHERE c.user_id = $1 AND c.advertiser_id = $3")
    pool = await get_pool()
    args = [uid, str(hours)] + ([adv_scope] if adv_scope else [])
    rows = await pool.fetch(sql, *args)
    out = []
    for r in rows:
        bids, wins = int(r["bids"]), int(r["wins"])
        imps, clicks = int(r["impressions"]), int(r["clicks"])
        spend = float(r["spend"])
        out.append({
            "id": str(r["id"]),
            "name": r["name"],
            "status": r["status"],
            "bids": bids, "wins": wins, "impressions": imps, "clicks": clicks,
            "spend": round(spend, 4),
            "win_rate": round(wins / bids * 100, 2) if bids else 0,
            "ctr": round(clicks / imps * 100, 3) if imps else 0,
            "ecpm": round(spend / imps * 1000, 2) if imps else 0,
        })
    return out
