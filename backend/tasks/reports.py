from celery_app import app
import logging
import os

logger = logging.getLogger(__name__)


@app.task(name="tasks.reports.daily_report")
def daily_report():
    """
    Runs every day at 08:00 UTC.
    Sends daily performance summary to Telegram.
    Report covers YESTERDAY's stats.
    """
    from database import SessionLocal, CampaignDB, AuctionLogDB, EventDB
    from sqlalchemy import select, func, and_
    from datetime import datetime, timezone, timedelta

    yesterday_start = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    yesterday_end = yesterday_start + timedelta(days=1)

    session = SessionLocal()
    try:
        # Total spend yesterday (RTB win prices)
        total_spend = session.execute(
            select(func.sum(AuctionLogDB.win_price)).where(
                and_(
                    AuctionLogDB.won == True,
                    AuctionLogDB.created_at >= yesterday_start,
                    AuctionLogDB.created_at < yesterday_end,
                )
            )
        ).scalar() or 0

        # Total revenue (conversion values)
        total_revenue = session.execute(
            select(func.sum(EventDB.value)).where(
                and_(
                    EventDB.event_name.in_(["lead", "purchase"]),
                    EventDB.created_at >= yesterday_start,
                    EventDB.created_at < yesterday_end,
                )
            )
        ).scalar() or 0

        roi = round(((float(total_revenue) - float(total_spend)) / float(total_spend)) * 100, 1) \
            if total_spend else 0

        date_str = yesterday_start.strftime("%d.%m.%Y")
        roi_emoji = "📈" if roi > 0 else "📉"

        message = (
            f"📊 Daily Report CentralDSP — {date_str}\n\n"
            f"💰 Summary:\n"
            f"   Spend:   ${float(total_spend):.2f}\n"
            f"   Revenue: ${float(total_revenue):.2f}\n"
            f"   ROI:     {roi_emoji} {roi:+.1f}%\n"
        )

        _send_telegram(message)
        logger.info(f"[Reports] Daily report sent: spend={total_spend} revenue={total_revenue} roi={roi}%")
        return {"spend": float(total_spend), "revenue": float(total_revenue), "roi": roi}

    finally:
        session.close()


def _send_telegram(message: str):
    import apprise
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID")
    if not tg_token or not tg_chat:
        logger.warning("[Reports] Telegram not configured, skipping notification")
        return
    ap = apprise.Apprise()
    ap.add(f"tgram://{tg_token}/{tg_chat}")
    ap.notify(title="", body=message)
