from celery_app import app
import logging

logger = logging.getLogger(__name__)


@app.task(name="tasks.sync.sync_ad_network_costs", bind=True, max_retries=3)
def sync_ad_network_costs(self):
    """
    Runs every 15 minutes.
    Pulls real spend data from connected ad networks (Push.House, Yandex, etc.)
    and updates cost in clicks table for accurate ROI calculation.
    Phase 3 (Ad Networks) will fully implement the adapters.
    Stub structure is here to keep Celery schedule working.
    """
    from database import SessionLocal, AdAccountDB
    from sqlalchemy import select
    from datetime import datetime, timezone

    session = SessionLocal()
    try:
        accounts = session.execute(
            select(AdAccountDB).where(AdAccountDB.is_active == True)
        ).scalars().all()

        synced = 0
        for account in accounts:
            try:
                adapter = _get_adapter(account.platform, account.credentials)
                if adapter is None:
                    continue

                stats = adapter.get_today_stats()
                account.balance = stats.get("balance")
                account.last_sync_at = datetime.now(timezone.utc)
                session.commit()
                synced += 1

            except Exception as e:
                logger.warning(f"[Sync] Failed to sync {account.platform}: {e}")
                continue

        logger.info(f"[Sync] sync_ad_network_costs: {synced}/{len(accounts)} accounts synced")
        return {"synced": synced}

    except Exception as exc:
        session.rollback()
        raise self.retry(exc=exc, countdown=120)
    finally:
        session.close()


def _get_adapter(platform: str, credentials: dict):
    """
    Returns the appropriate ad network adapter.
    Adapters are implemented in Phase 3 (backend/adapters/).
    """
    try:
        if platform == "push_house":
            from adapters.push_house import PushHouseAdapter
            return PushHouseAdapter(credentials.get("api_token", ""))
        # Phase 3: add Yandex, Kadam, PropellerAds, Google adapters
        return None
    except ImportError:
        return None
