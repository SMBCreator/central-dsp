import logging
import httpx
from celery_app import app
from database import SessionLocal
from models import AdAccount

logger = logging.getLogger(__name__)


class PushHouseAdapter:
    BASE_URL = "https://api.push.house/v1"

    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    def get_balance(self) -> float:
        try:
            r = httpx.get(f"{self.BASE_URL}/advertiser/balance", headers=self.headers, timeout=10)
            return float(r.json().get("balance", 0))
        except Exception as e:
            logger.warning(f"PushHouse balance failed: {e}")
            return 0.0

    def stop_campaign(self, external_id: str) -> bool:
        try:
            r = httpx.post(
                f"{self.BASE_URL}/advertiser/campaigns/{external_id}/stop",
                headers=self.headers, timeout=10
            )
            return r.status_code == 200
        except Exception as e:
            logger.warning(f"PushHouse stop_campaign failed: {e}")
            return False


def _get_adapter(account: AdAccount):
    creds = account.credentials or {}
    if account.platform == "push_house":
        token = creds.get("api_token", "")
        if token:
            return PushHouseAdapter(token)
    return None


@app.task(name="tasks.sync.sync_all_ad_networks", bind=True, max_retries=3)
def sync_all_ad_networks(self):
    """Sync balances from all connected ad networks every 15 minutes."""
    db = SessionLocal()
    try:
        accounts = db.query(AdAccount).filter(AdAccount.is_active == True).all()
        synced = 0
        for account in accounts:
            adapter = _get_adapter(account)
            if not adapter:
                continue
            try:
                balance = adapter.get_balance()
                account.balance = balance
                from sqlalchemy.sql import func
                account.last_sync_at = func.now()
                db.add(account)
                synced += 1
            except Exception as e:
                logger.warning(f"Sync failed for account {account.id}: {e}")
        db.commit()
        logger.info(f"[Sync] Synced {synced}/{len(accounts)} ad accounts")
    except Exception as exc:
        db.rollback()
        logger.error(f"[Sync] Error: {exc}")
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
