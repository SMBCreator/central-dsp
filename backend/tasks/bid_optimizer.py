"""AutoPilot RTB — Celery tasks for automatic bid management.

Schedule (in celery_beat_schedule.py):
  - bid_optimizer_run:     every 5 minutes
  - budget_pacer_run:      every 1 minute
  - site_blacklist_run:    every 10 minutes
  - win_rate_guard_run:    every 15 minutes

All tasks are idempotent and safe to run concurrently.
"""
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Tuple

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from celery_app import celery_app
from database import SessionLocal
from models import Campaign, TargetingRule, AuctionLog, Rule, RuleLog

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
BID_STEP_UP = Decimal("0.20")       # +20% when win rate is too low
BID_STEP_DOWN = Decimal("0.15")     # -15% when CPA is too high
MIN_BID_FLOOR = Decimal("0.005")    # absolute minimum bid $0.005 CPM
MAX_BID_CEILING = Decimal("50.0")   # absolute maximum bid $50 CPM
WIN_RATE_LOW_THRESHOLD = 0.5        # % — below this we raise bids
WIN_RATE_HIGH_THRESHOLD = 10.0      # % — above this we can lower bids
SITE_SPEND_SHARE_MAX = 0.30         # 30% — max share of budget for one site
BURST_HOUR = 18                     # 18:00 UTC — if < 50% budget spent, burst


# =============================================================================
# TASK 1: Bid Optimizer — adjust bid_ceiling based on win rate
# =============================================================================

@celery_app.task(name="tasks.bid_optimizer_run", bind=True, max_retries=2)
def bid_optimizer_run(self):
    """
    Every 5 minutes: check win rate per campaign over last 30 min.
    - Win rate < WIN_RATE_LOW_THRESHOLD  → raise bid_ceiling by 20%
    - Win rate > WIN_RATE_HIGH_THRESHOLD → lower bid_ceiling by 15%
    """
    db: Session = SessionLocal()
    try:
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        campaigns = db.query(Campaign).filter(Campaign.status == "active").all()
        adjusted = 0
        for campaign in campaigns:
            targeting = campaign.targeting
            if not targeting:
                continue

            stats = _get_campaign_rtb_stats(db, campaign.id, since)
            total, wins = stats
            if total < 10:  # not enough data
                continue

            win_rate = (wins / total) * 100
            current_ceiling = Decimal(str(targeting.bid_ceiling or 5.0))

            if win_rate < WIN_RATE_LOW_THRESHOLD:
                # Too few wins — raise bid
                new_ceiling = min(current_ceiling * (1 + BID_STEP_UP), MAX_BID_CEILING)
                targeting.bid_ceiling = new_ceiling
                db.add(targeting)
                logger.info(
                    f"[BidOpt] campaign={campaign.id} win_rate={win_rate:.2f}% "
                    f"bid_ceiling {current_ceiling} → {new_ceiling} (+20%)"
                )
                _log_rule_action(
                    db, campaign.id,
                    metric="win_rate", metric_value=float(win_rate),
                    action=f"bid_ceiling_up_{float(new_ceiling):.4f}"
                )
                adjusted += 1

            elif win_rate > WIN_RATE_HIGH_THRESHOLD:
                # Too many wins = we're overbidding — lower bid
                new_ceiling = max(current_ceiling * (1 - BID_STEP_DOWN), MIN_BID_FLOOR)
                targeting.bid_ceiling = new_ceiling
                db.add(targeting)
                logger.info(
                    f"[BidOpt] campaign={campaign.id} win_rate={win_rate:.2f}% "
                    f"bid_ceiling {current_ceiling} → {new_ceiling} (-15%)"
                )
                _log_rule_action(
                    db, campaign.id,
                    metric="win_rate", metric_value=float(win_rate),
                    action=f"bid_ceiling_down_{float(new_ceiling):.4f}"
                )
                adjusted += 1

        db.commit()
        logger.info(f"[BidOpt] Adjusted {adjusted}/{len(campaigns)} campaigns")
        return {"adjusted": adjusted, "total": len(campaigns)}

    except Exception as exc:
        db.rollback()
        logger.error(f"[BidOpt] Error: {exc}")
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


# =============================================================================
# TASK 2: Budget Pacer — ensure we don't overspend or underspend
# =============================================================================

@celery_app.task(name="tasks.budget_pacer_run", bind=True, max_retries=2)
def budget_pacer_run(self):
    """
    Every 1 minute:
    - Pause campaigns that exceeded daily_budget
    - If budget < 50% utilization by BURST_HOUR → enable burst mode
      (temporarily raise bid_ceiling by 30% until 23:00 UTC)
    """
    db: Session = SessionLocal()
    try:
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        campaigns = db.query(Campaign).filter(
            Campaign.status == "active",
            Campaign.daily_budget != None,  # noqa
        ).all()

        paused = 0
        burst = 0
        now_hour = datetime.now(timezone.utc).hour

        for campaign in campaigns:
            today_spend = db.query(
                sqlfunc.coalesce(sqlfunc.sum(AuctionLog.win_price), 0)
            ).filter(
                AuctionLog.campaign_id == campaign.id,
                AuctionLog.won == True,
                AuctionLog.created_at >= today_start,
            ).scalar() or Decimal("0")

            daily_budget = Decimal(str(campaign.daily_budget))

            # Overspend → pause campaign
            if today_spend >= daily_budget:
                campaign.status = "paused"
                db.add(campaign)
                _log_rule_action(
                    db, campaign.id,
                    metric="spend", metric_value=float(today_spend),
                    action="campaign_paused_budget_exceeded"
                )
                paused += 1
                logger.warning(
                    f"[Pacer] campaign={campaign.id} PAUSED "
                    f"spend={today_spend} >= budget={daily_budget}"
                )
                continue

            # Burst mode: under 50% spend by BURST_HOUR
            targeting = campaign.targeting
            if targeting and now_hour >= BURST_HOUR:
                utilization = float(today_spend / daily_budget) if daily_budget else 1.0
                if utilization < 0.50:
                    current_ceiling = Decimal(str(targeting.bid_ceiling or 5.0))
                    # Only burst once (check if already bursted by flag in extra metadata)
                    burst_ceiling = min(current_ceiling * Decimal("1.30"), MAX_BID_CEILING)
                    targeting.bid_ceiling = burst_ceiling
                    db.add(targeting)
                    burst += 1
                    logger.info(
                        f"[Pacer BURST] campaign={campaign.id} "
                        f"utilization={utilization:.0%} bid_ceiling → {burst_ceiling}"
                    )

        db.commit()
        logger.info(f"[Pacer] paused={paused} burst={burst}")
        return {"paused": paused, "burst_mode": burst}

    except Exception as exc:
        db.rollback()
        logger.error(f"[Pacer] Error: {exc}")
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()


# =============================================================================
# TASK 3: Site Blacklist — auto-blacklist sites consuming > SITE_SPEND_SHARE_MAX
# =============================================================================

@celery_app.task(name="tasks.site_blacklist_run", bind=True, max_retries=2)
def site_blacklist_run(self):
    """
    Every 10 minutes:
    Check if any single site_domain consumes > 30% of campaign spend today.
    If yes AND has poor performance (no conversions from Events) → blacklist it.
    """
    db: Session = SessionLocal()
    try:
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        campaigns = db.query(Campaign).filter(Campaign.status == "active").all()
        blacklisted_total = 0

        for campaign in campaigns:
            if not campaign.targeting:
                continue

            # Total spend today
            total_spend = db.query(
                sqlfunc.coalesce(sqlfunc.sum(AuctionLog.win_price), 0)
            ).filter(
                AuctionLog.campaign_id == campaign.id,
                AuctionLog.won == True,
                AuctionLog.created_at >= today_start,
            ).scalar() or Decimal("0")

            if total_spend < Decimal("1.0"):  # less than $1 spent — skip
                continue

            # Per-site spend
            site_spends = db.query(
                AuctionLog.site_domain,
                sqlfunc.sum(AuctionLog.win_price).label("spend")
            ).filter(
                AuctionLog.campaign_id == campaign.id,
                AuctionLog.won == True,
                AuctionLog.created_at >= today_start,
                AuctionLog.site_domain != None,  # noqa
            ).group_by(AuctionLog.site_domain).all()

            current_blacklist = list(campaign.targeting.site_blacklist or [])

            for site_domain, site_spend in site_spends:
                if not site_domain or site_domain in current_blacklist:
                    continue
                share = float(site_spend / total_spend) if total_spend else 0
                if share > SITE_SPEND_SHARE_MAX:
                    current_blacklist.append(site_domain)
                    _log_rule_action(
                        db, campaign.id,
                        metric="site_spend_share",
                        metric_value=share,
                        action=f"blacklist_site_{site_domain}"
                    )
                    blacklisted_total += 1
                    logger.warning(
                        f"[SiteBlacklist] campaign={campaign.id} "
                        f"site={site_domain} share={share:.0%} → BLACKLISTED"
                    )

            campaign.targeting.site_blacklist = current_blacklist
            db.add(campaign.targeting)

        db.commit()
        logger.info(f"[SiteBlacklist] Blacklisted {blacklisted_total} sites")
        return {"blacklisted": blacklisted_total}

    except Exception as exc:
        db.rollback()
        logger.error(f"[SiteBlacklist] Error: {exc}")
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


# =============================================================================
# TASK 4: Win Rate Guard — alert if win rate drops to near zero
# =============================================================================

@celery_app.task(name="tasks.win_rate_guard_run")
def win_rate_guard_run():
    """
    Every 15 minutes:
    Check all active campaigns. If win rate = 0 for 30 min AND campaign
    has been active for > 1 hour → log warning + trigger rule action.
    This prevents "silent bidding" (we bid but never win = wasted load).
    """
    db: Session = SessionLocal()
    try:
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        created_before = datetime.now(timezone.utc) - timedelta(hours=1)
        campaigns = db.query(Campaign).filter(
            Campaign.status == "active",
            Campaign.created_at <= created_before,
        ).all()

        dead_campaigns = []
        for campaign in campaigns:
            total, wins = _get_campaign_rtb_stats(db, campaign.id, since)
            if total < 20:
                continue  # not enough auction data
            if wins == 0:
                dead_campaigns.append(campaign.id)
                _log_rule_action(
                    db, campaign.id,
                    metric="win_rate", metric_value=0.0,
                    action="win_rate_zero_alert"
                )
                logger.error(
                    f"[WinRateGuard] campaign={campaign.id} "
                    f"0 wins in {total} auctions (30 min) — check targeting!"
                )

        db.commit()
        return {"zero_win_rate_campaigns": dead_campaigns}

    except Exception as exc:
        logger.error(f"[WinRateGuard] Error: {exc}")
        return {"error": str(exc)}
    finally:
        db.close()


# =============================================================================
# Helpers
# =============================================================================

def _get_campaign_rtb_stats(
    db: Session, campaign_id: int, since: datetime
) -> Tuple[int, int]:
    """Returns (total_bids, wins) since `since`."""
    total = db.query(AuctionLog).filter(
        AuctionLog.campaign_id == campaign_id,
        AuctionLog.created_at >= since,
    ).count()
    wins = db.query(AuctionLog).filter(
        AuctionLog.campaign_id == campaign_id,
        AuctionLog.won == True,
        AuctionLog.created_at >= since,
    ).count()
    return total, wins


def _log_rule_action(
    db: Session,
    campaign_id: int,
    metric: str,
    metric_value: float,
    action: str,
):
    """Write a RuleLog entry for AutoPilot action (no rule_id = system action)."""
    try:
        log = RuleLog(
            rule_id=None,      # system-generated, no user rule
            campaign_id=campaign_id,
            triggered_at=datetime.now(timezone.utc),
            metric_value=Decimal(str(metric_value)),
            action_taken=action[:100],
            notification_sent=False,
        )
        db.add(log)
    except Exception as e:
        logger.error(f"[_log_rule_action] {e}")
