import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_

logger = logging.getLogger(__name__)


class CentralBidOptimizer:
    """
    CentralBidder pricing engine.

    Strategy: CPA-target driven bidding.
    bid = cpa_target * historical_cvr(context) * safety_margin

    Context dimensions used for CVR lookup:
    - country_code
    - device_type
    - hour of day
    - site_domain (if known)

    Safety margin starts at 0.8 (conservative) and adjusts
    based on win rate: low win rate -> increase margin (bid more),
    high win rate with bad ROI -> decrease margin.
    """

    def __init__(self, db: Session):
        self.db = db

    def calculate_bid(
        self,
        campaign_id: int,
        context: Dict[str, Any],
        bid_floor: float = 0.01,
        bid_ceiling: float = 5.00,
    ) -> float:
        """
        Calculate optimal bid price for given context.
        Returns a float clamped between bid_floor and bid_ceiling.
        """
        try:
            cpa_target = self._get_cpa_target(campaign_id)
            if not cpa_target:
                # No CPA target set — use simple floor + 20% buffer
                return round(min(bid_floor * 1.2, bid_ceiling), 6)

            cvr = self._get_historical_cvr(campaign_id, context)
            safety_margin = self._get_safety_margin(campaign_id)

            raw_bid = cpa_target * cvr * safety_margin

            # Clamp to [floor, ceiling]
            bid = round(max(bid_floor, min(raw_bid, bid_ceiling)), 6)

            logger.debug(
                f"[CentralBidOptimizer] campaign={campaign_id} "
                f"cpa={cpa_target} cvr={cvr:.4f} margin={safety_margin} "
                f"raw={raw_bid:.4f} final={bid}"
            )
            return bid

        except Exception as e:
            logger.error(f"[CentralBidOptimizer ERROR] {e}")
            return round(bid_floor, 6)

    def _get_cpa_target(self, campaign_id: int) -> Optional[float]:
        """
        CPA target = offer_value from campaigns table.
        This is what the advertiser is willing to pay per conversion.
        """
        from database import CampaignDB
        row = self.db.execute(
            select(CampaignDB.offer_value).where(CampaignDB.id == campaign_id)
        ).scalar_one_or_none()
        return float(row) if row else None

    def _get_historical_cvr(self, campaign_id: int, context: Dict[str, Any]) -> float:
        """
        CVR = conversions / impressions for this campaign in this context.
        Context: country_code, device_type, hour.
        Falls back to campaign-level CVR if context-specific CVR not available.
        Minimum CVR floor: 0.001 (0.1%) to avoid zero bids.
        """
        from database import AuctionLogDB, EventDB

        # Time window: last 7 days
        since = datetime.now(timezone.utc) - timedelta(days=7)

        # Count impressions (won auctions) in context
        imp_query = select(func.count()).select_from(AuctionLogDB).where(
            and_(
                AuctionLogDB.campaign_id == campaign_id,
                AuctionLogDB.won == True,
                AuctionLogDB.created_at >= since,
            )
        )
        if context.get("country_code"):
            imp_query = imp_query.where(AuctionLogDB.geo_country == context["country_code"])
        if context.get("device_type"):
            imp_query = imp_query.where(AuctionLogDB.device_type == context["device_type"])

        impressions = self.db.execute(imp_query).scalar() or 0

        if impressions < 100:
            # Not enough data for context-level CVR — use campaign-level
            return self._get_campaign_level_cvr(campaign_id, since)

        # Count conversions in context
        conv_query = select(func.count()).select_from(EventDB).where(
            and_(
                EventDB.event_name.in_(["lead", "purchase", "registration"]),
                EventDB.created_at >= since,
                EventDB.metadata["campaign_id"].astext == str(campaign_id),
            )
        )
        if context.get("country_code"):
            conv_query = conv_query.where(EventDB.country_code == context["country_code"])
        if context.get("device_type"):
            conv_query = conv_query.where(EventDB.device_type == context["device_type"])

        conversions = self.db.execute(conv_query).scalar() or 0

        cvr = conversions / impressions if impressions > 0 else 0.001
        return max(cvr, 0.001)  # minimum floor

    def _get_campaign_level_cvr(self, campaign_id: int, since: datetime) -> float:
        """Fallback: campaign-level CVR (no context filtering)"""
        from database import AuctionLogDB, EventDB

        impressions = self.db.execute(
            select(func.count()).select_from(AuctionLogDB).where(
                and_(
                    AuctionLogDB.campaign_id == campaign_id,
                    AuctionLogDB.won == True,
                    AuctionLogDB.created_at >= since,
                )
            )
        ).scalar() or 0

        conversions = self.db.execute(
            select(func.count()).select_from(EventDB).where(
                and_(
                    EventDB.event_name.in_(["lead", "purchase", "registration"]),
                    EventDB.created_at >= since,
                    EventDB.metadata["campaign_id"].astext == str(campaign_id),
                )
            )
        ).scalar() or 0

        if impressions < 50:
            return 0.005  # Default 0.5% CVR when no data at all

        return max(conversions / impressions, 0.001)

    def _get_safety_margin(self, campaign_id: int) -> float:
        """
        Dynamic safety margin based on win rate.
        Win rate < 1%  -> increase bids (margin = 0.95)
        Win rate 1-5%  -> normal (margin = 0.80)
        Win rate > 10% -> we're winning too easily, might overpay (margin = 0.65)
        Win rate > 20% -> significantly reduce bids (margin = 0.50)
        """
        from database import AuctionLogDB

        since = datetime.now(timezone.utc) - timedelta(hours=24)

        total = self.db.execute(
            select(func.count()).select_from(AuctionLogDB).where(
                and_(
                    AuctionLogDB.campaign_id == campaign_id,
                    AuctionLogDB.created_at >= since,
                )
            )
        ).scalar() or 0

        if total < 100:
            return 0.80  # Default margin when no data

        won = self.db.execute(
            select(func.count()).select_from(AuctionLogDB).where(
                and_(
                    AuctionLogDB.campaign_id == campaign_id,
                    AuctionLogDB.won == True,
                    AuctionLogDB.created_at >= since,
                )
            )
        ).scalar() or 0

        win_rate = won / total

        if win_rate < 0.01:
            return 0.95   # Low win rate — bid more aggressively
        elif win_rate < 0.05:
            return 0.80   # Normal range
        elif win_rate < 0.10:
            return 0.70   # Slightly high, reduce a bit
        elif win_rate < 0.20:
            return 0.65   # High win rate, probably overpaying
        else:
            return 0.50   # Very high win rate — cut bids significantly
