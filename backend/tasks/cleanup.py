import logging
from datetime import datetime, timezone
from celery_app import app
from database import SessionLocal
from models import LinkDB

logger = logging.getLogger(__name__)


@app.task(name="tasks.cleanup.cleanup_expired_links", bind=True, max_retries=2)
def cleanup_expired_links(self):
    """
    Deactivate links that have passed their expires_at timestamp.
    Runs every hour.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        expired = db.query(LinkDB).filter(
            LinkDB.expires_at <= now,
            LinkDB.is_active == True
        ).all()

        count = len(expired)
        for link in expired:
            link.is_active = False

        db.commit()
        if count:
            logger.info(f"[Cleanup] Deactivated {count} expired links")
        return {"deactivated": count}
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
