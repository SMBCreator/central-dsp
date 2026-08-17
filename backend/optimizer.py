"""Buying optimizer: analyzes per-source performance and blocks weak traffic.

Runs from cron for campaigns with auto_optimize = TRUE. Sources are analyzed
across three axes: domain, supply partner, device type.

Rules (each requires a minimum impression volume to avoid noise):
  1. CTR anomaly high (click fraud):  ctr > 10x campaign avg AND conversions = 0
  2. CTR anomaly low (no engagement): imps >= 1000 AND ctr < 0.02%
  3. CPA overrun:   objective=conversions AND spend > 3x target_cpa AND conversions = 0
  4. CPC overrun:   objective=clicks AND target_cpc set AND effective CPC > 2x target
  5. Zero engagement (pixel): >= 20 ad sessions AND avg time on site < 3 sec
"""
from db import get_pool

MIN_IMPS_FOR_CTR = 500
MIN_IMPS_FOR_FRAUD = 200
MIN_SPEND_MULTIPLIER_CPA = 3.0
CPC_OVERRUN_FACTOR = 2.0
LOW_CTR_THRESHOLD = 0.0002   # 0.02%
FRAUD_CTR_FACTOR = 10.0
MIN_AD_SESSIONS = 20
MIN_AVG_TIME_SEC = 3.0


async def optimize_campaign(campaign: dict) -> list[dict]:
    """Analyze one campaign and insert optimizer_blocks. Returns created blocks."""
    pool = await get_pool()
    cid = campaign["id"]
    window = "72 hours"
    created: list[dict] = []

    # Campaign-level averages for anomaly detection
    camp = await pool.fetchrow(
        f"""SELECT COALESCE(SUM(impressions),0) AS imps, COALESCE(SUM(clicks),0) AS clicks,
                   COALESCE(SUM(spend),0) AS spend, COALESCE(SUM(conversions),0) AS convs
            FROM stats_hourly WHERE campaign_id = $1 AND ts_hour >= now() - interval '{window}'""",
        cid,
    )
    camp_imps = int(camp["imps"] or 0)
    if camp_imps < MIN_IMPS_FOR_CTR:
        return created  # not enough data yet
    camp_ctr = (int(camp["clicks"] or 0) / camp_imps) if camp_imps else 0

    async def _block(block_type: str, value: str, reason: str, metrics: dict) -> None:
        import json as _json
        row = await pool.fetchrow(
            """INSERT INTO optimizer_blocks (campaign_id, block_type, block_value, reason, metrics)
               VALUES ($1,$2,$3,$4,$5::jsonb)
               ON CONFLICT (campaign_id, block_type, block_value) DO NOTHING RETURNING id""",
            cid, block_type, value, reason, _json.dumps(metrics),
        )
        if row:
            created.append({"type": block_type, "value": value, "reason": reason})

    # ---- Per-domain analysis (bid_events has domain granularity) ----
    domains = await pool.fetch(
        f"""SELECT lower(domain) AS domain,
                   count(*) FILTER (WHERE event_type = 'impression') AS imps,
                   count(*) FILTER (WHERE event_type = 'click') AS clicks
            FROM bid_events
            WHERE campaign_id = $1 AND domain IS NOT NULL AND created_at >= now() - interval '{window}'
            GROUP BY 1 HAVING count(*) FILTER (WHERE event_type = 'impression') >= $2""",
        cid, MIN_IMPS_FOR_FRAUD,
    )
    for d in domains:
        imps, clicks = int(d["imps"]), int(d["clicks"])
        ctr = clicks / imps if imps else 0
        convs = await pool.fetchval(
            f"SELECT count(*) FROM conversions WHERE campaign_id = $1 AND lower(domain) = $2 AND created_at >= now() - interval '{window}'",
            cid, d["domain"],
        )
        # Rule 1: click-fraud pattern — abnormally high CTR, zero conversions
        if camp_ctr > 0 and ctr > camp_ctr * FRAUD_CTR_FACTOR and int(convs or 0) == 0 and clicks >= 10:
            await _block("domain", d["domain"],
                         f"Аномальный CTR {ctr*100:.2f}% (в {ctr/camp_ctr:.0f}x выше среднего) без конверсий — похоже на скликивание",
                         {"imps": imps, "clicks": clicks, "ctr": round(ctr, 5), "camp_ctr": round(camp_ctr, 5)})
            continue
        # Rule 2: dead inventory — high volume, near-zero CTR
        if imps >= MIN_IMPS_FOR_CTR * 2 and ctr < LOW_CTR_THRESHOLD:
            await _block("domain", d["domain"],
                         f"Нет вовлечённости: {imps} показов, CTR {ctr*100:.3f}%",
                         {"imps": imps, "clicks": clicks, "ctr": round(ctr, 5)})

    # ---- Per-SSP analysis ----
    partners = await pool.fetch(
        f"""SELECT supply_partner_id, COALESCE(SUM(impressions),0) AS imps, COALESCE(SUM(clicks),0) AS clicks,
                   COALESCE(SUM(spend),0) AS spend, COALESCE(SUM(conversions),0) AS convs
            FROM stats_hourly
            WHERE campaign_id = $1 AND supply_partner_id IS NOT NULL AND ts_hour >= now() - interval '{window}'
            GROUP BY 1 HAVING COALESCE(SUM(impressions),0) >= $2""",
        cid, MIN_IMPS_FOR_CTR,
    )
    objective = campaign.get("objective") or "clicks"
    target_cpa = float(campaign.get("target_cpa") or 0)
    target_cpc = float(campaign.get("target_cpc") or 0)
    for p in partners:
        imps, clicks = int(p["imps"]), int(p["clicks"])
        spend, convs = float(p["spend"] or 0), int(p["convs"] or 0)
        ctr = clicks / imps if imps else 0
        # Rule 3: CPA overrun per SSP
        if objective == "conversions" and target_cpa > 0 and convs == 0 and spend > target_cpa * MIN_SPEND_MULTIPLIER_CPA:
            await _block("supply_partner", str(p["supply_partner_id"]),
                         f"Потрачено ${spend:.2f} (>{MIN_SPEND_MULTIPLIER_CPA:.0f}x целевого CPA ${target_cpa:.2f}) без единой конверсии",
                         {"spend": round(spend, 2), "convs": 0, "target_cpa": target_cpa})
            continue
        # Rule 4: CPC overrun per SSP
        if objective == "clicks" and target_cpc > 0 and clicks >= 10:
            ecpc = spend / clicks
            if ecpc > target_cpc * CPC_OVERRUN_FACTOR:
                await _block("supply_partner", str(p["supply_partner_id"]),
                             f"Фактический CPC ${ecpc:.3f} превышает целевой ${target_cpc:.3f} более чем в {CPC_OVERRUN_FACTOR:.0f} раза",
                             {"ecpc": round(ecpc, 4), "target_cpc": target_cpc, "clicks": clicks})
                continue
        # Rule 2 for SSP: dead inventory
        if imps >= MIN_IMPS_FOR_CTR * 4 and ctr < LOW_CTR_THRESHOLD:
            await _block("supply_partner", str(p["supply_partner_id"]),
                         f"Нет вовлечённости: {imps} показов, CTR {ctr*100:.3f}%",
                         {"imps": imps, "clicks": clicks, "ctr": round(ctr, 5)})

    # ---- Pixel engagement analysis (Rule 5): time-on-site per source domain ----
    if campaign.get("pixel_id"):
        weak = await pool.fetch(
            f"""SELECT c.domain, count(DISTINCT pe.session_id) AS sessions,
                       COALESCE(SUM(pe.duration_sec),0)::float / NULLIF(count(DISTINCT pe.session_id),0) AS avg_time
                FROM pixel_events pe
                JOIN bid_events c ON c.bid_id = pe.clid AND c.event_type = 'bid'
                WHERE pe.pixel_id = $1 AND c.campaign_id = $2 AND c.domain IS NOT NULL
                  AND pe.created_at >= now() - interval '{window}'
                GROUP BY 1
                HAVING count(DISTINCT pe.session_id) >= $3""",
            campaign["pixel_id"], cid, MIN_AD_SESSIONS,
        )
        for w in weak:
            if float(w["avg_time"] or 0) < MIN_AVG_TIME_SEC:
                await _block("domain", (w["domain"] or "").lower(),
                             f"Мусорный трафик: {w['sessions']} визитов, среднее время на сайте {float(w['avg_time'] or 0):.1f} сек",
                             {"sessions": int(w["sessions"]), "avg_time_sec": round(float(w["avg_time"] or 0), 1)})

    return created


async def run_optimizer() -> dict:
    """Optimize all campaigns with auto_optimize enabled. Returns a summary."""
    pool = await get_pool()
    campaigns = await pool.fetch(
        "SELECT * FROM campaigns WHERE status = 'active' AND auto_optimize = TRUE"
    )
    total_blocks = 0
    details = []
    for camp in campaigns:
        created = await optimize_campaign(dict(camp))
        total_blocks += len(created)
        if created:
            details.append({"campaign_id": str(camp["id"]), "blocks": created})
    return {"campaigns_analyzed": len(campaigns), "blocks_created": total_blocks, "details": details}
