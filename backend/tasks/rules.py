from celery_app import celery_app
from database import SessionLocal
from models import RuleDB, CampaignDB, RuleLogDB, StatsDB
from sqlalchemy import func
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.rules.evaluate_all_rules")
def evaluate_all_rules():
    """Evaluate all active AutoPilot rules. Runs every 30 seconds."""
    db = SessionLocal()
    try:
        rules = db.query(RuleDB).filter(
            RuleDB.is_active == True,
            RuleDB.triggered == False
        ).all()

        triggered_count = 0
        for rule in rules:
            try:
                current_value = _get_metric_value(db, rule)
                if current_value is None:
                    continue

                if _check_condition(current_value, rule.condition, float(rule.threshold)):
                    _execute_action(db, rule, current_value)
                    triggered_count += 1
            except Exception as e:
                logger.error(f"Error evaluating rule {rule.id}: {e}")

        if triggered_count:
            logger.info(f"AutoPilot: triggered {triggered_count} rules")
        db.commit()
    except Exception as e:
        logger.error(f"evaluate_all_rules failed: {e}")
        db.rollback()
    finally:
        db.close()


def _get_metric_value(db: Session, rule: RuleDB) -> float | None:
    """Pull current metric value from stats table."""
    stats = db.query(StatsDB).filter(
        StatsDB.link_id.in_(
            db.query(StatsDB.link_id).join(
                CampaignDB, StatsDB.link_id == CampaignDB.id  # simplified — extend with proper join
            )
        )
    ).first()

    # Direct stats lookup by campaign
  