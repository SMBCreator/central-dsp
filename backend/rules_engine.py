"""Auto-rules engine: evaluates metric conditions and applies actions."""
from db import get_pool

_OPS = {"gt": ">", "lt": "<", "gte": ">=", "lte": "<="}


async def _campaign_metrics(pool, campaign_id: str, window_hours: int) -> dict:
    row = await pool.fetchrow(
        """
        SELECT
          COALESCE(SUM(bids), 0)        AS bids,
          COALESCE(SUM(wins), 0)        AS wins,
          COALESCE(SUM(impressions), 0) AS impressions,
          COALESCE(SUM(clicks), 0)      AS clicks,
          COALESCE(SUM(spend), 0)       AS spend
        FROM stats_hourly
        WHERE campaign_id = $1 AND ts_hour >= now() - ($2 || ' hours')::interval
        """,
        campaign_id, str(window_hours),
    )
    imps = int(row["impressions"])
    clicks = int(row["clicks"])
    bids = int(row["bids"])
    wins = int(row["wins"])
    spend = float(row["spend"])
    return {
        "impressions": imps,
        "clicks": clicks,
        "spend": spend,
        "ctr": (clicks / imps * 100) if imps else 0.0,
        "cpm": (spend / imps * 1000) if imps else 0.0,
        "cpc": (spend / clicks) if clicks else 0.0,
        "win_rate": (wins / bids * 100) if bids else 0.0,
    }


def _condition_met(value: float, operator: str, threshold: float) -> bool:
    if operator == "gt":
        return value > threshold
    if operator == "lt":
        return value < threshold
    if operator == "gte":
        return value >= threshold
    return value <= threshold


async def _apply_action(pool, rule: dict, campaign_id: str) -> str:
    action = rule["action"]
    value = float(rule["action_value"] or 0)
    if action == "pause_campaign":
        await pool.execute(
            "UPDATE campaigns SET status = 'paused', updated_at = now() WHERE id = $1", campaign_id
        )
        return f"Кампания {campaign_id} приостановлена"
    if action == "pause_line_item":
        await pool.execute(
            "UPDATE line_items SET status = 'paused', updated_at = now() WHERE campaign_id = $1", campaign_id
        )
        return f"Позиции кампании {campaign_id} приостановлены"
    if action in ("decrease_bid", "increase_bid"):
        pct = value if value > 0 else 10.0
        sign = "-" if action == "decrease_bid" else "+"
        await pool.execute(
            f"""
            UPDATE line_items
            SET bid_price = GREATEST(0.01, LEAST(COALESCE(max_bid, 1000), bid_price * (1 {sign} $2 / 100.0))),
                updated_at = now()
            WHERE campaign_id = $1 AND status = 'active'
            """,
            campaign_id, pct,
        )
        verb = "снижена" if action == "decrease_bid" else "повышена"
        return f"Ставка {verb} на {pct}% для кампании {campaign_id}"
    return f"Уведомление: правило '{rule['name']}' сработало для кампании {campaign_id}"


async def evaluate_rules_for_user(user_id: str) -> list[dict]:
    """Evaluate all active rules for a user. Returns list of triggered actions."""
    pool = await get_pool()
    rules = await pool.fetch(
        """
        SELECT * FROM rules
        WHERE user_id = $1 AND status = 'active'
          AND (last_triggered_at IS NULL OR last_triggered_at < now() - interval '1 hour')
        """,
        user_id,
    )
    triggered: list[dict] = []
    for rule in rules:
        if rule["campaign_id"]:
            campaign_ids = [str(rule["campaign_id"])]
        else:
            camps = await pool.fetch(
                "SELECT id FROM campaigns WHERE user_id = $1 AND status = 'active'", user_id
            )
            campaign_ids = [str(c["id"]) for c in camps]
        for cid in campaign_ids:
            metrics = await _campaign_metrics(pool, cid, rule["window_hours"])
            value = metrics.get(rule["metric"], 0.0)
            if metrics["impressions"] == 0 and rule["metric"] in ("ctr", "cpm"):
                continue
            if _condition_met(value, rule["operator"], float(rule["threshold"])):
                message = await _apply_action(pool, dict(rule), cid)
                await pool.execute(
                    "UPDATE rules SET last_triggered_at = now() WHERE id = $1", rule["id"]
                )
                triggered.append({
                    "rule_id": str(rule["id"]),
                    "rule_name": rule["name"],
                    "campaign_id": cid,
                    "metric": rule["metric"],
                    "value": round(value, 4),
                    "threshold": float(rule["threshold"]),
                    "message": message,
                })
    return triggered
