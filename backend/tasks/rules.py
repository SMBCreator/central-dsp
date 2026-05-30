from celery_app import app
import logging

logger = logging.getLogger(__name__)


@app.task(name="tasks.rules.evaluate_rules", bind=True, max_retries=3)
def evaluate_rules(self):
    """
    Runs every 30 seconds.
    Evaluates all active AutoPilot rules.
    Actions: autostop_campaign, disable_sub_id, notify,
             raise_bid (RTB), lower_bid (RTB), blacklist_site (RTB)
    """
    from database import SessionLocal, RuleDB, RuleLogDB, CampaignDB, AuctionLogDB
    from sqlalchemy import select, and_, func
    from datetime import datetime, timezone, timedelta
    import apprise
    import os

    session = SessionLocal()
    try:
        rules = session.execute(
            select(RuleDB).where(
                and_(RuleDB.is_active == True, RuleDB.triggered == False)
            )
        ).scalars().all()

        triggered_count = 0

        for rule in rules:
            metric_value = _get_metric_value(session, rule)
            if metric_value is None:
                continue

            fired = _check_condition(metric_value, rule.condition, float(rule.threshold))
            if not fired:
                continue

            # Execute action
            action_result = _execute_action(session, rule, metric_value)

            # Log the trigger
            log = RuleLogDB(
                rule_id=rule.id,
                campaign_id=rule.campaign_id,
                metric_value=metric_value,
                action_taken=rule.action,
                notification_sent=action_result.get("notified", False),
            )
            session.add(log)
            rule.triggered = True
            session.commit()
            triggered_count += 1

            logger.info(
                f"[AutoPilot] Rule {rule.id} fired: "
                f"metric={rule.metric} value={metric_value} "
                f"action={rule.action}"
            )

        return {"evaluated": len(rules), "triggered": triggered_count}

    except Exception as exc:
        session.rollback()
        logger.error(f"[AutoPilot evaluate_rules ERROR] {exc}")
        raise self.retry(exc=exc, countdown=30)
    finally:
        session.close()


def _get_metric_value(session, rule):
    """Calculate current metric value for a rule"""
    from database import EventDB, AuctionLogDB, ClickDB
    from sqlalchemy import select, func, and_
    from datetime import datetime, timezone, timedelta

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    cid = rule.campaign_id

    metric = rule.metric

    if metric == "win_rate":
        total = session.execute(
            select(func.count()).select_from(AuctionLogDB).where(
                and_(AuctionLogDB.campaign_id == cid, AuctionLogDB.created_at >= since)
            )
        ).scalar() or 0
        won = session.execute(
            select(func.count()).select_from(AuctionLogDB).where(
                and_(AuctionLogDB.campaign_id == cid, AuctionLogDB.won == True,
                     AuctionLogDB.created_at >= since)
            )
        ).scalar() or 0
        return (won / total * 100) if total > 0 else None

    if metric == "cpa":
        spend = session.execute(
            select(func.sum(AuctionLogDB.win_price)).where(
                and_(AuctionLogDB.campaign_id == cid, AuctionLogDB.won == True,
                     AuctionLogDB.created_at >= since)
            )
        ).scalar() or 0
        convs = session.execute(
            select(func.count()).select_from(EventDB).where(
                and_(
                    EventDB.event_name.in_(["lead", "purchase"]),
                    EventDB.metadata["campaign_id"].astext == str(cid),
                    EventDB.created_at >= since,
                )
            )
        ).scalar() or 0
        return (float(spend) / convs) if convs > 0 else None

    if metric == "roi":
        spend = session.execute(
            select(func.sum(AuctionLogDB.win_price)).where(
                and_(AuctionLogDB.campaign_id == cid, AuctionLogDB.won == True,
                     AuctionLogDB.created_at >= since)
            )
        ).scalar() or 0
        revenue = session.execute(
            select(func.sum(EventDB.value)).where(
                and_(
                    EventDB.event_name.in_(["lead", "purchase"]),
                    EventDB.metadata["campaign_id"].astext == str(cid),
                    EventDB.created_at >= since,
                )
            )
        ).scalar() or 0
        if float(spend) == 0:
            return None
        return round(((float(revenue) - float(spend)) / float(spend)) * 100, 2)

    return None


def _check_condition(value, condition, threshold) -> bool:
    ops = {"<": value < threshold, ">": value > threshold,
           "<=": value <= threshold, ">=": value >= threshold, "==": value == threshold}
    return ops.get(condition, False)


def _execute_action(session, rule, metric_value) -> dict:
    from database import CampaignDB, TargetingRuleDB
    from sqlalchemy import select
    import apprise
    import os

    result = {"notified": False}
    action = rule.action

    if action == "autostop_campaign":
        camp = session.get(CampaignDB, rule.campaign_id)
        if camp:
            camp.status = "stopped"
            session.commit()

    elif action == "raise_bid":
        targeting = session.execute(
            select(TargetingRuleDB).where(TargetingRuleDB.campaign_id == rule.campaign_id)
        ).scalar_one_or_none()
        if targeting:
            targeting.bid_ceiling = round(float(targeting.bid_ceiling) * 1.20, 4)
            session.commit()

    elif action == "lower_bid":
        targeting = session.execute(
            select(TargetingRuleDB).where(TargetingRuleDB.campaign_id == rule.campaign_id)
        ).scalar_one_or_none()
        if targeting:
            targeting.bid_ceiling = round(float(targeting.bid_ceiling) * 0.85, 4)
            session.commit()

    elif action == "notify":
        _send_notification(rule, metric_value)
        result["notified"] = True

    return result


def _send_notification(rule, metric_value):
    import apprise
    import os

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tg_token or not tg_chat:
        return

    ap = apprise.Apprise()
    ap.add(f"tgram://{tg_token}/{tg_chat}")
    ap.notify(
        title="⚠️ CentralDSP AutoPilot Alert",
        body=(
            f"Rule #{rule.id} triggered\n"
            f"Campaign: {rule.campaign_id}\n"
            f"Metric: {rule.metric} = {metric_value}\n"
            f"Condition: {rule.condition} {rule.threshold}\n"
            f"Action: {rule.action}"
        )
    )
