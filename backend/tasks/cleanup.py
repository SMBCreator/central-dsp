from celery_app import app
import logging

logger = logging.getLogger(__name__)


@app.task(name="tasks.cleanup.cleanup_expired_links")
def cleanup_expired_links():
    """
    Runs every hour.
    Marks expired links as inactive (is_active=False).
    Expired = expires_at < NOW() and is_active = True.
    We don't delete — keep for historical analytics.
    """
    from database import SessionLocal, LinkDB
    from sqlalchemy import select, and_
    from datetime import datetime, timezone

    session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        expired_links = session.execute(
            select(LinkDB).where(
                and_(
                    LinkDB.expires_at != None,
                    LinkDB.expires_at < now,
                    LinkDB.is_active == True,
                )
            )
        ).scalars().all()

        for link in expired_links:
            link.is_active = False

        session.commit()
        logger.info(f"[Cleanup] Deactivated {len(expired_links)} expired links")
        return {"deactivated": len(expired_links)}

    except Exception as e:
        session.rollback()
        logger.error(f"[Cleanup ERROR] {e}")
        return {"error": str(e)}
    finally:
        session.close()
