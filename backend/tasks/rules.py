import logging
from celery_app import app
from database import SessionLocal
from models import Campaign, Rule, RuleLog, Stat
from sqlalchemy import func
import httpx

logger = logging.getLogger(__name__)


def _get_campaign_metrics(db, campaign_id: int) -> dict:
    """Aggregate metrics for a campaign from stats table."""
    rows = db.query(Stat).join(Stat.link).filter(
        Stat.link.has(campaign_id=campaign_id)
    ).all()
    if not rows:
        return {}
    total_clicks = sum(r.clicks for r in rows)
    total_conv = sum(r.conversions for r in rows)
    total_cost = float(sum(r.cost or 0 for r in rows))
    total_revenue = float(sum(r.revenue or 0 for r in rows))
    cr = (total_conv / total_clicks * 100) if total_clicks else 0
    roi = ((total_revenue - total_cost) / total_cost * 100) if total_cost else 0
    epc = (total_revenue / total_clicks) if total_clicks else 0
    bounce_rate = (sum(r.bounces for r in rows) / total_clicks * 100) if total_clicks else 0
    return {
        "clicks": total_clicks,
        "conversions": total_conv,
        "cost": total_cost,
        "revenue": total_revenue,
        "cr": cr,
        "roi": roi,
        "epc": epc,
        "bounce_rate": bounce_rate,
    }


def _check_condition(value: float, condition: str, threshold: float) -> bool:
    ops = {"<": value < threshold, ">": value > threshold,
           "<=": value <= threshold, ">=": value >= threshold, "==": value == threshold}
    return ops.get(condition, False)


def _execute_action(db, campaign: Campaign, rule: Rule, metric_value: float):
    """Execute AutoPilot action and log it."""
    action = rule.action
    logger.info(f"[AutoPilot] Campaign {campaign.id} rule {rule.id}: {action} (metric={metric_value:.4f})")

    if action == "autostop_campaign":
        campaign.status = "stopped"
        db.add(campaign)

    elif action == "notify":
        _send_notification(campaign, rule, metric_value)

    elif action == "raise_bid_20pct":
        # RTB bid adjustment
        tr = campaign.targeting_rules[0] if campaign.targeting_rules else None
        if tr and tr.bid_ceiling:
            tr.bid_ceiling = float(tr.bid_ceiling) * 1.20
            db.add(tr)

    elif action == "lower_bid_15pct":
        tr = campaign.targeting_rules[0] if campaign.targeting_rules else None
        if tr and tr.bid_floor:
            tr.bid_floor = float(tr.bid_floor) * 0.85
            db.add(tr)

    # Log the triggered action
    log = RuleLog(
        rule_id=rule.id,
        campaign_id=campaign.id,
        metric_value=metric_value,
        action_taken=action,
        notification_sent=(action == "notify"),
    )
    db.add(log)
    rule.triggered = True
    db.add(rule)


def _send_notification(campaign: Campaign, rule: Rule, metric_value: float):
    """Send alert via Apprise (Telegram/Email/Webhook)."""
    try:
        import apprise
        from config import settings
        apobj = apprise.Apprise()
        if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
            apobj.add(f"tgram://{settings.TELEGRAM_BOT_TOKEN}/{settings.TELEGRAM_CHAT_ID}")
        if not apobj:
            return
        metric_val_str = f"{metric_value:.2f}"
        apobj.notify(
            title=f"\u26a0\ufe0f AutoPilot Alert — {campaign.name}",
            body=(
                f"Campaign: {campaign.name}\n"
                f"Rule: {rule.metric} {rule.condition} {rule.threshold}\n"
                f"Current value: {metric_val_str}\n"
                f"Action: {rule.action}"
            ),
        )
    except Exception as e:
        logger.error(f"Notification failed: {e}")


@app.task(name="tasks.rules.evaluate_all_rules", bind=True, max_retries=3)
def evaluate_all_rules(self):
    """Check all active AutoPilot rules every 30 seconds."""
    db = SessionLocal()
    try:
        active_campaigns = db.query(Campaign).filter(
            Campaign.status == "active"
        ).all()
        for campaign in active_campaigns:
            metrics = _get_campaign_metrics(db, campaign.id)
            if not metrics:
                continue
            for rule in campaign.rules:
                if not rule.is_active:
                    continue
                metric_value = metrics.get(rule.metric)
                if metric_value is None:
                    continue
                if _check_condition(float(metric_value), rule.condition, float(rule.threshold)):
                    _execute_action(db, campaign, rule, float(metric_value))
        db.commit()
        logger.info(f"[AutoPilot] Evaluated {len(active_campaigns)} campaigns")
    except Exception as exc:
        db.rollback()
        logger.error(f"[AutoPilot] Error: {exc}")
        raise self.retry(exc=exc, countdown=10)
    finally:
        db.close()
