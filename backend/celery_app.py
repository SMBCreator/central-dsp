from celery import Celery
from config import settings

app = Celery(
    "centraldsp",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "tasks.rules",
        "tasks.sync",
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
    # Queues
    task_default_queue="default",
    task_queues={
        "default": {},
        "rtb": {},
        "sync": {},
    },
    # Beat schedule — periodic tasks
    beat_schedule={
        "evaluate-rules-every-30s": {
            "task": "tasks.rules.evaluate_all_rules",
            "schedule": 30.0,
            "options": {"queue": "default"},
        },
        "sync-ad-network-costs-every-15m": {
            "task": "tasks.sync.sync_all_ad_networks",
            "schedule": 900.0,
            "options": {"queue": "sync"},
        },
        "daily-report-8am": {
            "task": "tasks.reports.send_daily_report",
            "schedule": 86400.0,  # override with crontab in prod
            "options": {"queue": "default"},
        },
        "cleanup-expired-links-hourly": {
            "task": "tasks.cleanup.cleanup_expired_links",
            "schedule": 3600.0,
            "options": {"queue": "default"},
        },
    },
)
