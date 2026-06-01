from __future__ import annotations
import logging
from datetime import datetime, timezone
from sqlalchemy import select, func
from celery_app import app
from database import SessionLocal
from models import Campaign, Click, Event, AuctionLog, CampaignStat

log = logging.getLogger(__name__)

def _bucket(dt):
    return dt.replace(minute=0, second=0, microsecond=0)

def _agg(db, cid, bucket, now):
    imps = db.execute(select(func.count(AuctionLog.id)).where(
        AuctionLog.campaign_id==cid, AuctionLog.won.is_(True),
        AuctionLog.created_at>=bucket, AuctionLog.created_at<now)).scalar() or 0
    spend = db.execute(select(func.coalesce(func.sum(AuctionLog.win_price),0)).where(
        AuctionLog.campaign_id==cid, AuctionLog.won.is_(True),
        AuctionLog.created_at>=bucket, AuctionLog.created_at<now)).scalar() or 0
    clicks = db.execute(select(func.count(Click.id)).join(Click.link).where(
        Click.link.has(campaign_id=cid),
        Click.created_at>=bucket, Click.created_at<now)).scalar() or 0
    convs = db.execute(select(func.count(Event.id)).join(Event.link).where(
        Event.event_name=="conversion", Event.link.has(campaign_id=cid),
        Event.created_at>=bucket, Event.created_at<now)).scalar() or 0
    rev = db.execute(select(func.coalesce(func.sum(Event.value),0)).join(Event.link).where(
        Event.event_name=="conversion", Event.link.has(campaign_id=cid),
        Event.created_at>=bucket, Event.created_at<now)).scalar() or 0
    return {"impressions":imps,"clicks":clicks,"conversions":convs,
            "spend":float(spend),"revenue":float(rev)}

@app.task(name="tasks.stats.aggregate_campaign_stats", bind=True, max_retries=3)
def aggregate_campaign_stats(self):
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        bucket = _bucket(now)
        camps = db.execute(select(Campaign).where(
            Campaign.status.in_(["active","paused"]))).scalars().all()
        for c in camps:
            data = _agg(db, c.id, bucket, now)
            stat = db.execute(select(CampaignStat).where(
                CampaignStat.campaign_id==c.id,
                CampaignStat.hour_bucket==bucket)).scalar_one_or_none()
            if stat is None:
                db.add(CampaignStat(campaign_id=c.id, hour_bucket=bucket, **data))
            else:
                for k, v in data.items(): setattr(stat, k, v)
                db.add(stat)
        db.commit()
        return {"updated":len(camps),"bucket":bucket.isoformat()}
    except Exception as exc:
        db.rollback(); raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()
