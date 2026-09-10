"""Auto-rules engine: evaluates metric conditions and applies actions."""
import logging

import conversion_attribution as attribution
from db import get_pool
from stats_math import CONVERSION_KPI_METRICS

log = logging.getLogger("rules")

_OPS = {"gt": ">", "lt": "<", "gte": ">=", "lte": "<="}


async def _campaign_metrics(pool, campaign_id: str, window_hours: int) -> dict:
    row = await pool.fetchrow(
        """
        SELECT
          COALESCE(SUM(bids), 0)        AS bids,
          COALESCE(SUM(wins), 0)        AS wins,
          COALESCE(SUM(impressions), 0) AS impressions,
          COALESCE(SUM(clicks), 0)      AS clicks,
          COALESCE(SUM(spend), 0)       AS spend,
          COALESCE(SUM(conversions), 0) AS conversions
        FROM stats_hourly
        WHERE campaign_id = $1 AND ts_hour >= now() - ($2 || ' hours')::interval
        """,
        campaign_id, str(window_hours),
    )
    budget_row = await pool.fetchrow(
        "SELECT total_budget, daily_budget, spent_total FROM campaigns WHERE id = $1", campaign_id
    )
    imps = int(row["impressions"])
    clicks = int(row["clicks"])
    bids = int(row["bids"])
    wins = int(row["wins"])
    spend = float(row["spend"])
    convs = int(row["conversions"])
    total_budget = float(budget_row["total_budget"] or 0) if budget_row else 0.0
    spent_total = float(budget_row["spent_total"] or 0) if budget_row else 0.0
    return {
        "impressions": imps,
        "clicks": clicks,
        "spend": spend,
        "conversions": convs,
        "ctr": (clicks / imps * 100) if imps else 0.0,
        "cpm": (spend / imps * 1000) if imps else 0.0,
        "cpc": (spend / clicks) if clicks else 0.0,
        "cpa": (spend / convs) if convs else 0.0,
        "cvr": (convs / clicks * 100) if clicks else 0.0,
        "win_rate": (wins / bids * 100) if bids else 0.0,
        "budget_spent_pct": (spent_total / total_budget * 100) if total_budget else 0.0,
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
    if action in ("decrease_daily_budget", "increase_daily_budget"):
        pct = value if value > 0 else 10.0
        sign = "-" if action == "decrease_daily_budget" else "+"
        await pool.execute(
            f"""
            UPDATE campaigns
            SET daily_budget = GREATEST(1, daily_budget * (1 {sign} $2 / 100.0)), updated_at = now()
            WHERE id = $1 AND daily_budget IS NOT NULL
            """,
            campaign_id, pct,
        )
        verb = "снижен" if action == "decrease_daily_budget" else "повышен"
        return f"Дневной бюджет {verb} на {pct}% для кампании {campaign_id}"
    if action == "blacklist_domains":
        min_imps = int(value) if value and value > 0 else 500
        rows = await pool.fetch(
            """
            SELECT domain, SUM(impressions) AS imps, SUM(clicks) AS clicks
            FROM domain_stats_hourly
            WHERE campaign_id = $1 AND ts_hour >= now() - ($2 || ' hours')::interval
            GROUP BY domain
            HAVING SUM(impressions) >= $3 AND SUM(clicks) = 0
            """,
            campaign_id, str(rule["window_hours"]), min_imps,
        )
        owner = await pool.fetchval("SELECT user_id FROM campaigns WHERE id = $1", campaign_id)
        added = 0
        for r in rows:
            res = await pool.execute(
                """
                INSERT INTO domain_lists (user_id, list_type, domain, source)
                VALUES ($1, 'black', $2, 'auto_rule')
                ON CONFLICT (user_id, list_type, domain) DO NOTHING
                """,
                owner, r["domain"],
            )
            if res.endswith("1"):
                added += 1
        return f"Автоблэклист: {added} площадок без кликов (>= {min_imps} показов) для кампании {campaign_id}"
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
    # F-02/B14c: по CPA/CVR/conversions действуем только при исправной атрибуции
    # (покрытие >= 0.8 за окно правила); иначе правило пропускается, а не
    # останавливает кампанию по «нулю конверсий», которого нет.
    kpi_ok_cache: dict[int, bool] = {}
    for rule in rules:
        if rule["metric"] in CONVERSION_KPI_METRICS:
            wh = int(rule["window_hours"])
            if wh not in kpi_ok_cache:
                kpi_ok_cache[wh] = await attribution.kpi_trustworthy_for_owner(pool, user_id, wh)
            if not kpi_ok_cache[wh]:
                log.warning("rule %s skipped: conversion attribution coverage low for user %s", rule["id"], user_id)
                continue
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
            if metrics["impressions"] == 0 and rule["metric"] in ("ctr", "cpm", "cpc", "cpa", "cvr"):
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
