"""Celery configuration for Central DSP."""
from celery import Celery
from celery.schedules import crontab
from backend.config import settings

celery_app = Celery(
    "centraldsp",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "backend.tasks.rules",
        "backend.tasks.sync",
        "backend.tasks.reports",
        "backend.tasks.cleanup",
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
        "backend.tasks.rules.*": {"queue": "default"},
        "backend.tasks.sync.*": {"queue": "sync"},
        "backend.tasks.reports.*": {"queue": "default"},
        "backend.tasks.cleanup.*": {"queue": "default"},
    },
    beat_schedule={
        # AutoPilot rules: every 30 seconds
        "evaluate-rules": {
            "task": "backend.tasks.rules.evaluate_all_rules",
            "schedule": 30.0,
        },
        # Sync ad network costs: every 15 minutes
        "sync-ad-costs": {
            "task": "backend.tasks.sync.sync_all_ad_accounts",
            "schedule": 900.0,
        },
        # Daily report at 08:00 UTC
        "daily-report": {
            "task": "backend.tasks.reports.send_daily_report",
            "schedule": crontab(hour=8, minute=0),
        },
        # Cleanup expired links: every hour
        "cleanup-expired": {
            "task": "backend.tasks.cleanup.cleanup_expired_links",
            "schedule": crontab(minute=0),
        },
    },
)
