"""Celery configuration for Central DSP."""
from celery import Celery
from celery.schedules import crontab
from config import settings  # fix: no 'backend.' prefix inside container

celery_app = Celery(
    "centraldsp",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "tasks.rules",      # fix: was 'backend.tasks.rules'
        "tasks.sync",
        "tasks.reports",
        "tasks.cleanup",
        "tasks.bid_optimizer",  # new: RTB bid optimization task
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "tasks.rules.*":         {"queue": "default"},
        "tasks.sync.*":          {"queue": "sync"},
        "tasks.reports.*":       {"queue": "default"},
        "tasks.cleanup.*":       {"queue": "default"},
        "tasks.bid_optimizer.*": {"queue": "default"},
    },
    beat_schedule={
        # AutoPilot rules: every 30 seconds
        "evaluate-rules": {
            "task": "tasks.rules.evaluate_all_rules",
            "schedule": 30.0,
        },
        # RTB Bid optimizer: every 5 minutes
        "optimize-bids": {
            "task": "tasks.bid_optimizer.optimize_all_campaigns",
            "schedule": 300.0,
        },
        # Sync ad network costs: every 15 minutes
        "sync-ad-costs": {
            "task": "tasks.sync.sync_all_ad_accounts",
            "schedule": 900.0,
        },
        # Daily report at 08:00 UTC
        "daily-report": {
            "task": "tasks.reports.send_daily_report",
            "schedule": crontab(hour=8, minute=0),
        },
        # Cleanup expired links + old auction logs: every hour
        "cleanup-expired": {
            "task": "tasks.cleanup.cleanup_expired_links",
            "schedule": crontab(minute=0),
        },
    },
)
