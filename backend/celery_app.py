"""Celery configuration for Central DSP."""
from celery import Celery
from celery.schedules import crontab
from config import settings

celery_app = Celery(
    "centraldsp",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "tasks.rules",
        "tasks.sync",
        "tasks.reports",
        "tasks.cleanup",
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
        "tasks.rules.*":   {"queue": "default"},
        "tasks.sync.*":    {"queue": "sync"},
        "tasks.reports.*": {"queue": "default"},
        "tasks.cleanup.*": {"queue": "default"},
    },
    beat_schedule={
        "evaluate-rules": {
            "task": "tasks.rules.evaluate_all_rules",
            "schedule": 30.0,
        },
        "sync-ad-costs": {
            "task": "tasks.sync.sync_all_ad_accounts",
            "schedule": 900.0,
        },
        "daily-report": {
            "task": "tasks.reports.send_daily_report",
            "schedule": crontab(hour=8, minute=0),
        },
        "cleanup-expired": {
            "task": "tasks.cleanup.cleanup_expired_links",
            "schedule": crontab(minute=0),
        },
    },
)

app = celery_app
