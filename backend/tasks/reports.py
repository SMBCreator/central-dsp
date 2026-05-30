import logging
from celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="tasks.reports.send_daily_report", bind=True, max_retries=2)
def send_daily_report(self):
    """
    Sends daily performance report to Telegram/Email at 08:00 UTC.
    Format:
        📊 Daily Report CentralDSP — DD.MM.YYYY
        💰 Total yesterday:
           Spend: $X  Revenue: $X  ROI: +X%
        🏆 Top campaigns: ...
        ⚠️ Needs attention: ...
    Implemented in Phase 4 (Step 4).
    """
    logger.info("[Reports] Daily report — placeholder (Phase 4)")
    # TODO Phase 4: query yesterday stats, format message, send via Apprise
    return {"sent": False}
