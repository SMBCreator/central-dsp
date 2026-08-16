from __future__ import annotations
import logging
from decimal import Decimal
from sqlalchemy import select, update
from celery_app import app
from database import SessionLocal
from models import Campaign, AuctionLog

log = logging.getLogger(__name__)

@app.task(name="tasks.budget.record_win_spend", bind=True, max_retries=5)
def record_win_spend(self, campaign_id: int, win_price: float, auction_log_id: int | None = None):
    db = SessionLocal()
    try:
        if auction_log_id:
            row = db.execute(select(AuctionLog).where(
                AuctionLog.id==auction_log_id)).scalar_one_or_none()
            if row:
                row.won = True
                row.win_price = Decimal(str(win_price))
                db.add(row)
        from tasks.autopilot import _check_budget
        c = db.execute(select(Campaign).where(
            Campaign.id==campaign_id)).scalar_one_or_none()
        if c: _check_budget(db, c)
        db.commit()
        return {"ok": True}
    except Exception as exc:
        db.rollback(); raise self.retry(exc=exc, countdown=5)
    finally:
        db.close()

@app.task(name="tasks.budget.reset_daily_budgets", bind=True, max_retries=3)
def reset_daily_budgets(self):
    """Runs at 00:00 UTC - reactivates campaigns paused by daily budget."""
    db = SessionLocal()
    try:
        r = db.execute(update(Campaign).where(
            Campaign.status=="paused",
            Campaign.daily_budget.isnot(None),
            Campaign.total_budget.is_(None)
        ).values(status="active"))
        db.commit()
        return {"reactivated": r.rowcount}
    except Exception as exc:
        db.rollback(); raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
