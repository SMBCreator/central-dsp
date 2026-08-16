from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy import select, func
from celery_app import app
from database import SessionLocal
from models import Campaign, Rule, RuleLog, TargetingRule, Event, AuctionLog

log = logging.getLogger(__name__)

def _today():
    n = datetime.now(timezone.utc)
    return n.replace(hour=0, minute=0, second=0, microsecond=0)

def _window():
    return datetime.now(timezone.utc) - timedelta(hours=1)

def _metric(db, cid, metric):
    s = _window()
    if metric == "win_rate":
        total = db.execute(select(func.count(AuctionLog.id)).where(
            AuctionLog.campaign_id==cid, AuctionLog.created_at>=s)).scalar() or 0
        won = db.execute(select(func.count(AuctionLog.id)).where(
            AuctionLog.campaign_id==cid, AuctionLog.won.is_(True),
            AuctionLog.created_at>=s)).scalar() or 0
        return None if total==0 else Decimal(str(won/total*100))
    if metric == "cpa":
        spend = Decimal(str(db.execute(select(func.coalesce(func.sum(AuctionLog.win_price),0)).where(
            AuctionLog.campaign_id==cid, AuctionLog.won.is_(True),
            AuctionLog.created_at>=s)).scalar() or 0))
        convs = db.execute(select(func.count(Event.id)).join(Event.link).where(
            Event.event_name=="conversion", Event.link.has(campaign_id=cid),
            Event.created_at>=s)).scalar() or 0
        return None if convs==0 else spend/convs
    if metric == "roi":
        spend = Decimal(str(db.execute(select(func.coalesce(func.sum(AuctionLog.win_price),0)).where(
            AuctionLog.campaign_id==cid, AuctionLog.won.is_(True),
            AuctionLog.created_at>=s)).scalar() or 0))
        rev = Decimal(str(db.execute(select(func.coalesce(func.sum(Event.value),0)).join(Event.link).where(
            Event.event_name=="conversion", Event.link.has(campaign_id=cid),
            Event.created_at>=s)).scalar() or 0))
        return None if spend==0 else (rev-spend)/spend*100
    if metric == "spend":
        return Decimal(str(db.execute(select(func.coalesce(func.sum(AuctionLog.win_price),0)).where(
            AuctionLog.campaign_id==cid, AuctionLog.won.is_(True),
            AuctionLog.created_at>=_today())).scalar() or 0))
    return None

def _notify(msg):
    try:
        from config import settings
        import apprise
        if not getattr(settings, "telegram_bot_token", None): return
        a = apprise.Apprise()
        a.add(f"tgram://{settings.telegram_bot_token}/{settings.telegram_chat_id}")
        a.notify(body=msg, title="Central DSP AutoPilot")
    except Exception: pass

def _check_budget(db, campaign):
    if campaign.status != "active": return
    spent_today = Decimal(str(db.execute(select(func.coalesce(func.sum(AuctionLog.win_price),0)).where(
        AuctionLog.campaign_id==campaign.id, AuctionLog.won.is_(True),
        AuctionLog.created_at>=_today())).scalar() or 0))
    spent_total = Decimal(str(db.execute(select(func.coalesce(func.sum(AuctionLog.win_price),0)).where(
        AuctionLog.campaign_id==campaign.id, AuctionLog.won.is_(True))).scalar() or 0))
    if campaign.daily_budget and spent_today >= Decimal(str(campaign.daily_budget)):
        campaign.status = "paused"; db.add(campaign)
        _notify(f"[Budget] '{campaign.name}' paused - daily budget exhausted")
    elif campaign.total_budget and spent_total >= Decimal(str(campaign.total_budget)):
        campaign.status = "stopped"; db.add(campaign)
        _notify(f"[Budget] '{campaign.name}' stopped - total budget exhausted")

@app.task(name="tasks.autopilot.evaluate_rules", bind=True, max_retries=3)
def evaluate_rules(self):
    db = SessionLocal()
    try:
        camps = db.execute(select(Campaign).where(
            Campaign.status.in_(["active","paused"]))).scalars().all()
        fired = 0
        for c in camps:
            _check_budget(db, c)
            if c.status != "active": continue
            for rule in db.execute(select(Rule).where(
                    Rule.campaign_id==c.id, Rule.is_active.is_(True))).scalars().all():
                try:
                    val = _metric(db, c.id, rule.metric)
                    if val is None: continue
                    thr = Decimal(str(rule.threshold))
                    ops = {"<":val<thr,">":val>thr,"<=":val<=thr,">=":val>=thr,"==":val==thr}
                    if not ops.get(rule.condition, False): continue
                    act = rule.action.strip().lower()
                    if act == "stop_campaign":
                        c.status = "stopped"; db.add(c); desc = "stopped"
                    elif act == "pause_campaign":
                        c.status = "paused"; db.add(c); desc = "paused"
                    elif act.startswith("increase_bid"):
                        pct = Decimal(act.split()[1]) if len(act.split())>1 else Decimal("20")
                        t = db.scalar(select(TargetingRule).where(TargetingRule.campaign_id==c.id))
                        if t: t.bid_ceiling = t.bid_ceiling*(1+pct/100); db.add(t)
                        desc = f"bid+{pct}%"
                    elif act.startswith("decrease_bid"):
                        pct = Decimal(act.split()[1]) if len(act.split())>1 else Decimal("20")
                        t = db.scalar(select(TargetingRule).where(TargetingRule.campaign_id==c.id))
                        if t: t.bid_ceiling = max(t.bid_ceiling*(1-pct/100), t.bid_floor); db.add(t)
                        desc = f"bid-{pct}%"
                    else:
                        desc = "notify"
                        _notify(f"Rule fired: {rule.metric} {rule.condition} {thr} | {c.name}")
                    rule.triggered = True; db.add(rule)
                    db.add(RuleLog(rule_id=rule.id, campaign_id=c.id,
                                   metric_value=val, action_taken=desc, notification_sent=False))
                    fired += 1
                except Exception as e: log.error("Rule %d: %s", rule.id, e)
        db.commit()
        return {"campaigns":len(camps),"fired":fired}
    except Exception as exc:
        db.rollback(); raise self.retry(exc=exc, countdown=10)
    finally:
        db.close()
