import logging
from celery_app import app
from database import SessionLocal
from models import CampaignDB, TargetingRuleDB, AuctionLogDB
from sqlalchemy import func

logger = logging.getLogger(__name__)


@app.task(name="tasks.bidding.optimize_all_bids", bind=True, max_retries=2)
def optimize_all_bids(self):
    """
    RTB Bid Optimizer — runs every 5 minutes.
    Adjusts bid_floor/bid_ceiling per campaign based on win rate and ROI.
    Phase 7 implementation.
    """
    db = SessionLocal()
    try:
        campaigns = db.query(CampaignDB).filter(CampaignDB.status == "active").all()
        optimized = 0
        for campaign in campaigns:
            result = BidOptimizer(db).maybe_adjust(campaign)
            if result:
                optimized += 1
        db.commit()
        logger.info(f"[BidOptimizer] Adjusted bids for {optimized} campaigns")
        return {"optimized": optimized}
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()


class BidOptimizer:
    """
    Core bidding logic.
    MVP strategy: adjust bid based on win rate and CPA vs target.
    """
    WIN_RATE_TOO_LOW  = 0.01   # < 1% → raise bid ceiling
    WIN_RATE_TOO_HIGH = 0.20   # > 20% → lower bid (we’re overbidding)
    CPA_OVER_TARGET   = 1.5    # CPA > target * 1.5 → lower bid
    BID_STEP          = 0.15   # 15% adjustment per cycle

    def __init__(self, db):
        self.db = db

    def calculate_bid(self, campaign: CampaignDB, context: dict) -> float:
        """
        Calculate optimal bid for a specific context (geo, device, hour, site).
        Formula (MVP): bid = cpa_target * historical_cvr(context) * safety_margin
        """
        targeting = self.db.query(TargetingRuleDB).filter(
            TargetingRuleDB.campaign_id == campaign.id
        ).first()

        if not targeting:
            return 0.10  # default safe bid

        cvr = self._get_historical_cvr(campaign.id, context)
        cpa_target = float(campaign.offer_value) if campaign.offer_value else 5.0
        bid = cpa_target * cvr * 0.8  # 20% safety margin

        # Clamp to targeting limits
        return max(
            float(targeting.bid_floor),
            min(bid, float(targeting.bid_ceiling))
        )

    def maybe_adjust(self, campaign: CampaignDB) -> bool:
        """Check last hour stats and adjust bid_ceiling if needed"""
        targeting = self.db.query(TargetingRuleDB).filter(
            TargetingRuleDB.campaign_id == campaign.id
        ).first()
        if not targeting:
            return False

        stats = self._get_last_hour_stats(campaign.id)
        if not stats or stats["auctions"] < 100:  # not enough data
            return False

        win_rate = stats["wins"] / stats["auctions"]
        changed = False

        if win_rate < self.WIN_RATE_TOO_LOW:
            # Raise ceiling by BID_STEP, stay within 3x floor
            new_ceiling = min(
                float(targeting.bid_ceiling) * (1 + self.BID_STEP),
                float(targeting.bid_floor) * 3
            )
            targeting.bid_ceiling = new_ceiling
            logger.info(f"Campaign {campaign.id}: win_rate {win_rate:.1%} → raised bid_ceiling to {new_ceiling:.4f}")
            changed = True

        elif win_rate > self.WIN_RATE_TOO_HIGH:
            # Lower ceiling by BID_STEP, never go below floor
            new_ceiling = max(
                float(targeting.bid_ceiling) * (1 - self.BID_STEP),
                float(targeting.bid_floor) * 1.1
            )
            targeting.bid_ceiling = new_ceiling
            logger.info(f"Campaign {campaign.id}: win_rate {win_rate:.1%} → lowered bid_ceiling to {new_ceiling:.4f}")
            changed = True

        return changed

    def _get_historical_cvr(self, campaign_id: int, context: dict) -> float:
        """Get conversion rate for a specific context from auction_logs + events"""
        # TODO Phase 7: implement proper CVR lookup by context dimensions
        return 0.02  # 2% default CVR until Phase 7

    def _get_last_hour_stats(self, campaign_id: int) -> dict | None:
        """Get auction stats for the last hour"""
        from datetime import datetime, timezone, timedelta
        hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)

        row = self.db.query(
            func.count(AuctionLogDB.id).label("auctions"),
            func.sum(
                func.cast(AuctionLogDB.won, Integer) if False else AuctionLogDB.won.cast("int")
            ).label("wins")
        ).filter(
            AuctionLogDB.campaign_id == campaign_id,
            AuctionLogDB.created_at >= hour_ago
        ).first()

        if not row or not row.auctions:
            return None
        return {"auctions": row.auctions, "wins": row.wins or 0}
