from celery_app import app
from bid_optimizer import CentralBidOptimizer
import logging
import os
import redis

logger = logging.getLogger(__name__)


@app.task(name="tasks.bidding.auto_adjust_bids", bind=True, max_retries=3)
def auto_adjust_bids(self):
    """
    Runs every 5 minutes.
    Recalculates bid_adjustments for all active campaigns based on:
    - Win rate trends (last 24h)
    - CVR by country/device/hour
    - Budget utilization (if daily_budget set and < 50% used by 18:00 — increase bids)
    Results are stored in targeting_rules.bid_floor / bid_ceiling
    and cached in Redis for CentralBidder to pick up.
    """
    from database import SessionLocal, CampaignDB, TargetingRuleDB
    from sqlalchemy import select
    from datetime import datetime, timezone

    session = SessionLocal()
    try:
        campaigns = session.execute(
            select(CampaignDB).where(CampaignDB.status == "active")
        ).scalars().all()

        optimizer = CentralBidOptimizer(session)
        adjusted = 0

        for camp in campaigns:
            targeting = session.execute(
                select(TargetingRuleDB).where(TargetingRuleDB.campaign_id == camp.id)
            ).scalar_one_or_none()

            if not targeting:
                continue

            # Calculate new bid ceiling based on safety margin
            margin = optimizer._get_safety_margin(camp.id)
            new_ceiling = round(float(targeting.bid_ceiling) * (margin / 0.80), 4)

            # Clamp to reasonable bounds
            new_ceiling = max(float(targeting.bid_floor) * 1.1, min(new_ceiling, 10.0))

            if abs(new_ceiling - float(targeting.bid_ceiling)) > 0.001:
                targeting.bid_ceiling = new_ceiling
                session.commit()
                adjusted += 1

                logger.info(
                    f"[CentralBidder] Adjusted campaign={camp.id} "
                    f"new_ceiling={new_ceiling} margin={margin}"
                )

        # Invalidate campaign cache so CentralBidder picks up new prices
        if adjusted > 0:
            _invalidate_cache()

        logger.info(f"[CentralBidder] auto_adjust_bids complete: {adjusted} campaigns adjusted")
        return {"adjusted": adjusted}

    except Exception as exc:
        session.rollback()
        logger.error(f"[CentralBidder auto_adjust_bids ERROR] {exc}")
        raise self.retry(exc=exc, countdown=60)
    finally:
        session.close()


@app.task(name="tasks.bidding.invalidate_campaign_cache")
def invalidate_campaign_cache():
    """
    Invalidates CentralBidder Redis campaign feed cache.
    Runs every 60 seconds as a safety net.
    Also called directly when campaigns/creatives are updated via API.
    """
    _invalidate_cache()
    return {"status": "ok"}


def _invalidate_cache():
    r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    r.delete("central:rtb:campaign_feed")
