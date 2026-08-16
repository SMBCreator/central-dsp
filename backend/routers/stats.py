from fastapi import APIRouter, Depends

from auth import current_user
from db import get_pool

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/overview")
async def overview(hours: int = 24, user: dict = Depends(current_user)):
    """Totals + deltas for the dashboard."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT
          COALESCE(SUM(s.bids), 0)        AS bids,
          COALESCE(SUM(s.wins), 0)        AS wins,
          COALESCE(SUM(s.impressions), 0) AS impressions,
          COALESCE(SUM(s.clicks), 0)      AS clicks,
          COALESCE(SUM(s.spend), 0)       AS spend
        FROM stats_hourly s
        JOIN campaigns c ON c.id = s.campaign_id
        WHERE c.user_id = $1 AND s.ts_hour >= now() - ($2 || ' hours')::interval
        """,
        user["id"], str(hours),
    )
    counts = await pool.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM campaigns WHERE user_id = $1 AND status = 'active')       AS active_campaigns,
          (SELECT count(*) FROM campaigns WHERE user_id = $1)                             AS total_campaigns,
          (SELECT count(*) FROM creatives WHERE user_id = $1)                             AS total_creatives,
          (SELECT count(*) FROM supply_partners WHERE user_id = $1 AND status = 'active') AS active_partners
        """,
        user["id"],
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
    pool = await get_pool()
    filters = "AND s.campaign_id = $3" if campaign_id else ""
    args = [user["id"], str(hours)] + ([campaign_id] if campaign_id else [])
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
        *args,
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


@router.get("/by-campaign")
async def by_campaign(hours: int = 168, user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
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
        """,
        user["id"], str(hours),
    )
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


@router.get("/by-partner")
async def by_partner(hours: int = 168, user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT p.id, p.name,
               COALESCE(SUM(s.bids), 0) AS bids, COALESCE(SUM(s.wins), 0) AS wins,
               COALESCE(SUM(s.impressions), 0) AS impressions,
               COALESCE(SUM(s.clicks), 0) AS clicks, COALESCE(SUM(s.spend), 0) AS spend
        FROM supply_partners p
        LEFT JOIN stats_hourly s ON s.supply_partner_id = p.id AND s.ts_hour >= now() - ($2 || ' hours')::interval
        WHERE p.user_id = $1
        GROUP BY p.id, p.name
        ORDER BY bids DESC
        """,
        user["id"], str(hours),
    )
    return [
        {
            "id": str(r["id"]), "name": r["name"],
            "bids": int(r["bids"]), "wins": int(r["wins"]),
            "impressions": int(r["impressions"]), "clicks": int(r["clicks"]),
            "spend": round(float(r["spend"]), 4),
            "win_rate": round(int(r["wins"]) / int(r["bids"]) * 100, 2) if int(r["bids"]) else 0,
        }
        for r in rows
    ]
