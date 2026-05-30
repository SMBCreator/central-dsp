from celery import Celery
from celery.schedules import crontab
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# CentralDSP Celery application
app = Celery(
    "central_dsp",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "tasks.rules",
        "tasks.sync",
        "tasks.bidding",
        "tasks.reports",
        "tasks.cleanup",
    ],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,           # Re-queue on worker crash
    worker_prefetch_multiplier=1,  # Fair distribution
    task_routes={
        "tasks.rules.*":   {"queue": "default"},
        "tasks.sync.*":    {"queue": "sync"},
        "tasks.bidding.*": {"queue": "rtb"},
        "tasks.reports.*": {"queue": "default"},
        "tasks.cleanup.*": {"queue": "default"},
    },
    beat_schedule={
        # AutoPilot: check rules every 30 seconds
        "evaluate-rules": {
            "task": "tasks.rules.evaluate_rules",
            "schedule": 30.0,
        },
        # Sync ad network costs every 15 minutes
        "sync-ad-costs": {
            "task": "tasks.sync.sync_ad_network_costs",
            "schedule": crontab(minute="*/15"),
        },
        # CentralBidder: recalculate bid adjustments every 5 minutes
        "adjust-bids": {
            "task": "tasks.bidding.auto_adjust_bids",
            "schedule": crontab(minute="*/5"),
        },
        # Daily report at 08:00 UTC
        "daily-report": {
            "task": "tasks.reports.daily_report",
            "schedule": crontab(hour=8, minute=0),
        },
        # Cleanup expired links every hour
        "cleanup-expired": {
            "task": "tasks.cleanup.cleanup_expired_links",
            "schedule": crontab(minute=0),
        },
        # Invalidate CentralBidder campaign cache every 60 seconds
        # (backup in case API update didn't trigger invalidation)
        "invalidate-bidder-cache": {
            "task": "tasks.bidding.invalidate_campaign_cache",
            "schedule": 60.0,
        },
    },
)
