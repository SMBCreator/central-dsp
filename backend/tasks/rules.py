import logging
from celery_app import app
from database import SessionLocal
from models import RuleDB, CampaignDB, RuleLogDB, StatsDB
from sqlalchemy import func

logger = logging.getLogger(__name__)


@app.task(name="tasks.rules.evaluate_all_rules", bind=True, max_retries=3)
def evaluate_all_rules(self):
    """
    AutoPilot: evaluate all active rules every 30 seconds.
    For each active campaign — check metrics against thresholds.
    """
    db = SessionLocal()
    try:
        rules = db.query(RuleDB).filter(
            RuleDB.is_active == True,
            RuleDB.triggered == False
        ).all()

        triggered_count = 0
        for rule in rules:
            try:
                metric_value = _get_metric_value(db, rule)
                if metric_value is None:
                    continue

                if _check_condition(metric_value, rule.condition, float(rule.threshold)):
                    _execute_action(db, rule, metric_value)
                    triggered_count += 1
            except Exception as e:
                logger.error(f"Rule {rule.id} evaluation failed: {e}")

        if triggered_count:
            logger.info(f"AutoPilot: {triggered_count} rules triggered")
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(f"evaluate_all_rules failed: {exc}")
        raise self.retry(exc=exc, countdown=10)
    finally:
        db.close()


def _get_metric_value(db, rule: RuleDB) -> float | None:
    """Calculate current metric value for a rule from stats table"""
    stats = db.query(StatsDB).join(StatsDB.link).filter(
        # filter by campaign via link.campaign_id
    ).all()

    # TODO: replace with TimeSeries queries from events/clicks tables
    # (will be proper after Phase 4 / Step 0 migration)
    totals = db.query(
        func.sum(StatsDB.clicks).label("clicks"),
        func.sum(StatsDB.conversions).label("conversions"),
        func.sum(StatsDB.cost).label("cost"),
        func.sum(StatsDB.revenue).label("revenue"),
        func.sum(StatsDB.bounces).label("bounces"),
    ).join(StatsDB.link).filter(
        # CampaignDB.id == rule.campaign_id
    ).first()

    if not totals or not totals.clicks:
        return None

    metric_map = {
        "clicks":      float(totals.clicks or 0),
        "conversions": float(totals.conversions or 0),
        "cost":        float(totals.cost or 0),
        "revenue":     float(totals.revenue or 0),
        "roi":         ((float(totals.revenue or 0) - float(totals.cost or 0)) / float(totals.cost) * 100)
                        if totals.cost else 0,
        "cr":          (float(totals.conversions or 0) / float(totals.clicks) * 100)
                        if totals.clicks else 0,
        "bounce_rate": (float(totals.bounces or 0) / float(totals.clicks) * 100)
                        if totals.clicks else 0,
    }
    return metric_map.get(rule.metric)


def _check_condition(value: float, condition: str, threshold: float) -> bool:
    ops = {"<": value < threshold, ">": value > threshold,
           "<=": value <= threshold, ">=": value >= threshold, "==": value == threshold}
    return ops.get(condition, False)


def _execute_action(db, rule: RuleDB, metric_value: float):
    """Execute rule action and log it"""
    logger.info(f"[AutoPilot] Rule {rule.id}: {rule.metric}={metric_value:.2f} {rule.condition} {rule.threshold} → {rule.action}")

    if rule.action == "autostop_campaign":
        db.query(CampaignDB).filter(CampaignDB.id == rule.campaign_id).update({"status": "stopped"})
        rule.triggered = True

    # Log the trigger
    log = RuleLogDB(
        rule_id=rule.id,
        campaign_id=rule.campaign_id,
        metric_value=metric_value,
        action_taken=rule.action,
        notification_sent=False,
    )
    db.add(log)
    # TODO: send Apprise notification
