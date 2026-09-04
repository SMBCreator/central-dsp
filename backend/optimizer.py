"""Buying optimizer: analyzes performance across EVERY observable dimension
and blocks weak traffic sources.

Dimensions (anything the SSP sends us or the Central Pixel reports back):
  domain, supply_partner, device_type, geo_country, os, browser, ad_type,
  creative, line_item, hour (of day, UTC)

Rules (each requires minimum volume to avoid noise):
  1. CTR anomaly high (click fraud):  ctr > 10x campaign avg AND conversions = 0
  2. CTR anomaly low (no engagement): high volume AND ctr < 0.02%
  3. CPA overrun:  target_cpa set AND spend > 3x target_cpa AND conversions = 0
  4. CPC overrun:  target_cpc set AND effective CPC > 2x target
  5. Junk traffic (pixel): >= 20 ad sessions AND avg time on site < 3 sec
  6. CPM overrun:  target_cpm set AND effective eCPM > 2x target
  7. CPV overrun (video): target_cpv set AND spend / completed views > 2x target
  8. Low VTR (video): >= 100 video starts AND completion rate < 15%

Spend per dimension comes from win events (clear_price), conversions from the
Central Pixel joined back through cdsp_clid -> bid_id, so every rule works on
every dimension uniformly.
"""
import json as _json

from db import get_pool

MIN_IMPS_FOR_CTR = 500
MIN_IMPS_FOR_FRAUD = 200
MIN_SPEND_MULTIPLIER_CPA = 3.0
CPC_OVERRUN_FACTOR = 2.0
LOW_CTR_THRESHOLD = 0.0002   # 0.02%
FRAUD_CTR_FACTOR = 10.0
MIN_AD_SESSIONS = 20
MIN_AVG_TIME_SEC = 3.0
CPM_OVERRUN_FACTOR = 2.0
CPV_OVERRUN_FACTOR = 2.0
MIN_VIDEO_STARTS = 100
MIN_VTR = 0.15               # 15% completion rate floor
WINDOW = "72 hours"

# block_type -> (SQL expression over bid_events alias "be", volume multiplier)
# Higher multiplier = coarser dimension = demand more data before blocking it.
DIMENSIONS: dict[str, tuple[str, int]] = {
    "domain": ("lower(be.domain)", 1),
    "supply_partner": ("be.supply_partner_id::text", 2),
    "device_type": ("be.device_type", 4),
    "geo_country": ("lower(be.geo_country)", 4),
    "os": ("be.os", 4),
    "browser": ("be.browser", 4),
    "ad_type": ("be.ad_type", 4),
    "creative": ("be.creative_id::text", 2),
    "line_item": ("be.line_item_id::text", 2),
    "hour": ("extract(hour from be.created_at)::int::text", 6),
}


async def optimize_campaign(campaign: dict) -> list[dict]:
    """Analyze one campaign across all dimensions. Returns created blocks."""
    pool = await get_pool()
    cid = campaign["id"]
    created: list[dict] = []

    camp = await pool.fetchrow(
        f"""SELECT COALESCE(SUM(impressions),0) AS imps, COALESCE(SUM(clicks),0) AS clicks
            FROM stats_hourly WHERE campaign_id = $1 AND ts_hour >= now() - interval '{WINDOW}'""",
        cid,
    )
    camp_imps = int(camp["imps"] or 0)
    if camp_imps < MIN_IMPS_FOR_CTR:
        return created  # not enough data yet
    camp_ctr = (int(camp["clicks"] or 0) / camp_imps) if camp_imps else 0

    target_cpa = float(campaign.get("target_cpa") or 0)
    target_cpc = float(campaign.get("target_cpc") or 0)
    target_cpm = float(campaign.get("target_cpm") or 0)
    target_cpv = float(campaign.get("target_cpv") or 0)
    objective = campaign.get("objective") or "clicks"

    async def _block(block_type: str, value: str, reason: str, metrics: dict) -> None:
        row = await pool.fetchrow(
            """INSERT INTO optimizer_blocks (campaign_id, block_type, block_value, reason, metrics)
               VALUES ($1,$2,$3,$4,$5::jsonb)
               ON CONFLICT (campaign_id, block_type, block_value) DO NOTHING RETURNING id""",
            cid, block_type, value, reason, _json.dumps(metrics),
        )
        if row:
            created.append({"type": block_type, "value": value, "reason": reason})

    DIM_LABELS = {
        "domain": "площадка", "supply_partner": "SSP", "device_type": "тип устройства",
        "geo_country": "гео", "os": "ОС", "browser": "браузер", "ad_type": "формат",
        "creative": "креатив", "line_item": "строка закупки", "hour": "час (UTC)",
    }

    for block_type, (dim_expr, volume_mult) in DIMENSIONS.items():
        label = DIM_LABELS[block_type]
        min_imps = MIN_IMPS_FOR_FRAUD * volume_mult
        # Per-dimension-value: impressions, clicks, spend (win clear prices)
        rows = await pool.fetch(
            f"""SELECT {dim_expr} AS val,
                       count(*) FILTER (WHERE be.event_type = 'impression') AS imps,
                       count(*) FILTER (WHERE be.event_type = 'click') AS clicks,
                       count(*) FILTER (WHERE be.event_type = 'video_start') AS vstarts,
                       count(*) FILTER (WHERE be.event_type = 'video_complete') AS vcompletes,
                       COALESCE(SUM(be.clear_price) FILTER (WHERE be.event_type = 'win'), 0) / 1000.0 AS spend
                FROM bid_events be
                WHERE be.campaign_id = $1 AND {dim_expr} IS NOT NULL
                  AND NOT be.is_test
                  AND be.created_at >= now() - interval '{WINDOW}'
                GROUP BY {dim_expr}
                HAVING count(*) FILTER (WHERE be.event_type = 'impression') >= $2""",
            cid, min_imps,
        )
        # Conversions per dimension value (pixel, joined back through cdsp_clid -> bid_id)
        conv_rows = await pool.fetch(
            f"""SELECT {dim_expr} AS val, count(*) AS convs
                FROM conversions cv
                JOIN bid_events be ON be.bid_id = cv.clid AND be.event_type = 'bid'
                WHERE be.campaign_id = $1 AND {dim_expr} IS NOT NULL
                  AND cv.created_at >= now() - interval '{WINDOW}'
                GROUP BY {dim_expr}""",
            cid,
        )
        convs_by_val = {str(r["val"]): int(r["convs"]) for r in conv_rows}
        # Never block the campaign's ONLY value in a dimension (would kill delivery)
        if len(rows) < 2 and block_type in ("device_type", "geo_country", "os", "browser", "ad_type"):
            continue
        for r in rows:
            val = str(r["val"])
            if val in ("unknown", "none", ""):
                continue
            imps, clicks = int(r["imps"]), int(r["clicks"])
            spend, convs = float(r["spend"] or 0), convs_by_val.get(val, 0)
            ctr = clicks / imps if imps else 0
            # Rule 1: click-fraud — abnormally high CTR, zero conversions
            if camp_ctr > 0 and ctr > camp_ctr * FRAUD_CTR_FACTOR and convs == 0 and clicks >= 10:
                await _block(block_type, val,
                             f"Аномальный CTR {ctr*100:.2f}% (в {ctr/camp_ctr:.0f}x выше среднего) без конверсий — похоже на скликивание ({label})",
                             {"imps": imps, "clicks": clicks, "ctr": round(ctr, 5), "camp_ctr": round(camp_ctr, 5)})
                continue
            # Rule 2: dead inventory — high volume, near-zero CTR
            if imps >= min_imps * 4 and ctr < LOW_CTR_THRESHOLD:
                await _block(block_type, val,
                             f"Нет вовлечённости: {imps} показов, CTR {ctr*100:.3f}% ({label})",
                             {"imps": imps, "clicks": clicks, "ctr": round(ctr, 5)})
                continue
            # Rule 3: CPA overrun — spend without conversions
            if (objective == "conversions" or target_cpa > 0) and target_cpa > 0 \
                    and convs == 0 and spend > target_cpa * MIN_SPEND_MULTIPLIER_CPA:
                await _block(block_type, val,
                             f"Потрачено ${spend:.2f} (>{MIN_SPEND_MULTIPLIER_CPA:.0f}x целевого CPA ${target_cpa:.2f}) без единой конверсии ({label})",
                             {"spend": round(spend, 2), "convs": 0, "target_cpa": target_cpa})
                continue
            # Rule 4: CPC overrun
            if target_cpc > 0 and clicks >= 10:
                ecpc = spend / clicks if clicks else 0
                if ecpc > target_cpc * CPC_OVERRUN_FACTOR:
                    await _block(block_type, val,
                                 f"Фактический CPC ${ecpc:.3f} превышает целевой ${target_cpc:.3f} более чем в {CPC_OVERRUN_FACTOR:.0f} раза ({label})",
                                 {"ecpc": round(ecpc, 4), "target_cpc": target_cpc, "clicks": clicks})
                    continue
            # Rule 6: CPM overrun — paying too much for this dimension's impressions
            if target_cpm > 0 and imps >= min_imps:
                ecpm = spend / imps * 1000 if imps else 0
                if ecpm > target_cpm * CPM_OVERRUN_FACTOR:
                    await _block(block_type, val,
                                 f"Фактический eCPM ${ecpm:.2f} превышает целевой ${target_cpm:.2f} более чем в {CPM_OVERRUN_FACTOR:.0f} раза ({label})",
                                 {"ecpm": round(ecpm, 3), "target_cpm": target_cpm, "imps": imps})
                    continue
            # Video rules — only where this dimension actually serves video
            vstarts, vcompletes = int(r["vstarts"]), int(r["vcompletes"])
            if vstarts >= MIN_VIDEO_STARTS:
                vtr = vcompletes / vstarts
                # Rule 7: CPV overrun — cost per completed view too high
                if target_cpv > 0 and vcompletes > 0:
                    ecpv = spend / vcompletes
                    if ecpv > target_cpv * CPV_OVERRUN_FACTOR:
                        await _block(block_type, val,
                                     f"Фактический CPV ${ecpv:.3f} (за досмотр) превышает целевой ${target_cpv:.3f} более чем в {CPV_OVERRUN_FACTOR:.0f} раза ({label})",
                                     {"ecpv": round(ecpv, 4), "target_cpv": target_cpv, "vcompletes": vcompletes})
                        continue
                # Rule 8: low VTR — video started but almost never watched
                if vtr < MIN_VTR:
                    await _block(block_type, val,
                                 f"Низкая досматриваемость видео: {vstarts} стартов, VTR {vtr*100:.1f}% (порог {MIN_VTR*100:.0f}%) ({label})",
                                 {"vstarts": vstarts, "vcompletes": vcompletes, "vtr": round(vtr, 3)})

    # ---- Rule 5: pixel engagement (time on site) — per any dimension ----
    if campaign.get("pixel_id"):
        for block_type, (dim_expr, _mult) in DIMENSIONS.items():
            if block_type == "hour":
                continue  # time-of-day engagement is too noisy for auto-blocks
            label = DIM_LABELS[block_type]
            weak = await pool.fetch(
                f"""SELECT {dim_expr} AS val, count(DISTINCT pe.session_id) AS sessions,
                           COALESCE(SUM(pe.duration_sec),0)::float / NULLIF(count(DISTINCT pe.session_id),0) AS avg_time
                    FROM pixel_events pe
                    JOIN bid_events be ON be.bid_id = pe.clid AND be.event_type = 'bid'
                    WHERE pe.pixel_id = $1 AND be.campaign_id = $2 AND {dim_expr} IS NOT NULL
                      AND pe.created_at >= now() - interval '{WINDOW}'
                    GROUP BY {dim_expr}
                    HAVING count(DISTINCT pe.session_id) >= $3""",
                campaign["pixel_id"], cid, MIN_AD_SESSIONS,
            )
            for w in weak:
                val = str(w["val"])
                if val in ("unknown", "none", ""):
                    continue
                if float(w["avg_time"] or 0) < MIN_AVG_TIME_SEC:
                    await _block(block_type, val,
                                 f"Мусорный трафик: {w['sessions']} визитов, среднее время на сайте {float(w['avg_time'] or 0):.1f} сек ({label})",
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
