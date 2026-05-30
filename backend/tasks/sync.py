import logging
from celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="tasks.sync.sync_all_ad_networks", bind=True, max_retries=3)
def sync_all_ad_networks(self):
    """
    Sync real spend from all connected ad networks every 15 minutes.
    Updates cost in clicks table for accurate ROI calculation.
    Implemented in Phase 3 (Step 3) when AdNetwork adapters are built.
    """
    logger.info("[Sync] Ad network cost sync — placeholder (Phase 3)")
    # TODO Phase 3:
    # for account in get_active_ad_accounts():
    #     adapter = get_adapter(account.platform, account.credentials)
    #     stats = adapter.get_campaign_stats(today)
    #     update_costs_in_db(stats)
    return {"synced": 0}
