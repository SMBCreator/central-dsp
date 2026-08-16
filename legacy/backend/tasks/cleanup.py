import logging
from datetime import datetime, timezone
from celery_app import app
from database import SessionLocal
from models import Link

logger = logging.getLogger(__name__)


@app.task(name="tasks.cleanup.cleanup_expired_links")
def cleanup_expired_links():
    """Deactivate links whose expires_at has passed."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        expired = db.query(Link).filter(
            Link.is_active == True,
            Link.expires_at != None,
            Link.expires_at < now,
        ).all()
        for link in expired:
            link.is_active = False
            db.add(link)
        db.commit()
        if expired:
            logger.info(f"[Cleanup] Deactivated {len(expired)} expired links")
    except Exception as e:
        db.rollback()
        logger.error(f"[Cleanup] Error: {e}")
    finally:
        db.close()
