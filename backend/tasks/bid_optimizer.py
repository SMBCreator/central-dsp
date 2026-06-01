"""RTB Bid Optimization Celery task.

Runs every 5 minutes via beat schedule.
Adjusts bid_ceiling/bid_floor in targeting_rules based on
historical win_rate, CPA, and hourly performance.
"""
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from celery_app import celery_app
from database import SessionLocal
from models import Campaign, TargetingRule, AuctionLog, Stat, Rule, RuleLog

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.bid_optimizer.optimize_all_campaigns", bind=True, max_retries=3)
def optimize_all_campaigns(self):
    """Evaluate bid performance for all active campaigns and adjust targeting."""
    db = SessionLocal()
    try:
        campaigns = db.query(Campaign).filter(Campaign.status == "active").all()
        optimized = 0
        for campaign in campaigns:
            try:
                _optimize_campaign(db, campaign)
                optimized += 1
            except Exception as exc:
                logger.error(f"[BidOptimizer] campaign={campaign.id} error: {exc}")
        logger.info(f"[BidOptimizer] optimized {optimized}/{len(campaigns)} campaigns")
    except Exception as exc:
        logger.error(f"[BidOptimizer] fatal error: {exc}")
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


def _optimize_campaign(db, campaign: Campaign):
    """Core bid optimization logic for a single campaign."""
    targeting = db.query(TargetingRule).filter(
        TargetingRule.campaign_id == campaign.id
    ).first()
    if not targeting:
        return  # no targeting configured yet

    now = datetime.now(timezone.utc)
    window = now - timedelta(hours=1)  # last 1 hour window

    logs = db.query(AuctionLog).filter(
        AuctionLog.campaign_id == campaign.id,
        AuctionLog.created_at >= window,
    ).all()

    if not logs:
        return  # no data yet

    total_bids = len(logs)
    wins = sum(1 for l in logs if l.won)
    win_rate = wins / total_bids if total_bids > 0 else 0

    total_spend = float(sum(l.win_price or 0 for l in logs if l.won))

    # Get conversions in same window from Events
    from models import Event
    conversions = db.query(Event).filter(
        Event.event_name == "conversion",
        Event.created_at >= window,
    ).count()

    cpa = total_spend / conversions if conversions > 0 else None
    cpa_target = float(campaign.offer_value or 5.0)  # fallback $5 CPA target

    current_ceiling = float(targeting.bid_ceiling or 5.0)
    current_floor = float(targeting.bid_floor or 0.01)
    changed = False

    # Rule 1: Win rate too low → raise ceiling by 20%
    if win_rate < 0.01 and total_bids >= 50:
        new_ceiling = min(current_ceiling * 1.20, 50.0)  # cap at $50 CPM
        logger.info(
            f"[BidOptimizer] campaign={campaign.id} "
            f"win_rate={win_rate:.3f} < 1% → ceiling {current_ceiling:.4f} → {new_ceiling:.4f}"
        )
        targeting.bid_ceiling = Decimal(str(round(new_ceiling, 6)))
        changed = True

    # Rule 2: CPA too high → lower ceiling by 15%
    elif cpa and cpa > cpa_target * 1.5:
        new_ceiling = max(current_ceiling * 0.85, current_floor * 2)
        logger.info(
            f"[BidOptimizer] campaign={campaign.id} "
            f"cpa={cpa:.2f} > target*1.5 → ceiling {current_ceiling:.4f} → {new_ceiling:.4f}"
        )
        targeting.bid_ceiling = Decimal(str(round(new_ceiling, 6)))
        changed = True

    # Rule 3: Good win rate + CPA on target → slight ceiling increase
    elif win_rate > 0.05 and cpa and cpa < cpa_target * 0.8:
        new_ceiling = min(current_ceiling * 1.10, 50.0)
        logger.info(
            f"[BidOptimizer] campaign={campaign.id} "
            f"performing well → ceiling {current_ceiling:.4f} → {new_ceiling:.4f}"
        )
        targeting.bid_ceiling = Decimal(str(round(new_ceiling, 6)))
        changed = True

    if changed:
        db.add(targeting)
        db.commit()
        logger.info(
            f"[BidOptimizer] campaign={campaign.id} updated: "
            f"floor={targeting.bid_floor} ceiling={targeting.bid_ceiling}"
        )
