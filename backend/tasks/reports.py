import logging
from celery_app import app
from database import SessionLocal
from models import Campaign, Stat

logger = logging.getLogger(__name__)


@app.task(name="tasks.reports.send_daily_report")
def send_daily_report():
    """Send daily summary to Telegram at 8:00 UTC."""
    db = SessionLocal()
    try:
        from config import settings
        if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID):
            logger.info("[Report] Telegram not configured, skipping")
            return

        campaigns = db.query(Campaign).filter(Campaign.status != "archived").all()
        total_cost = 0.0
        total_revenue = 0.0
        top = []

        for c in campaigns:
            rows = db.query(Stat).join(Stat.link).filter(
                Stat.link.has(campaign_id=c.id)
            ).all()
            cost = float(sum(r.cost or 0 for r in rows))
            revenue = float(sum(r.revenue or 0 for r in rows))
            total_cost += cost
            total_revenue += revenue
            roi = ((revenue - cost) / cost * 100) if cost else 0
            top.append({"name": c.name, "roi": roi, "cost": cost, "revenue": revenue})

        top_sorted = sorted(top, key=lambda x: x["roi"], reverse=True)[:5]
        overall_roi = ((total_revenue - total_cost) / total_cost * 100) if total_cost else 0

        lines = [
            "\U0001f4ca Daily Report — Central DSP",
            "",
            f"\U0001f4b0 Total spend: ${total_cost:.2f}  Revenue: ${total_revenue:.2f}  ROI: {overall_roi:+.1f}%",
            "",
            "\U0001f3c6 Top campaigns:",
        ]
        for i, c in enumerate(top_sorted, 1):
            lines.append(f"  {i}. {c['name']} — ROI {c['roi']:+.1f}%  (${c['cost']:.2f} spent)")

        message = "\n".join(lines)

        import apprise
        apobj = apprise.Apprise()
        apobj.add(f"tgram://{settings.TELEGRAM_BOT_TOKEN}/{settings.TELEGRAM_CHAT_ID}")
        apobj.notify(title="\U0001f4ca Central DSP Daily Report", body=message)
        logger.info("[Report] Daily report sent")
    except Exception as e:
        logger.error(f"[Report] Failed: {e}")
    finally:
        db.close()
