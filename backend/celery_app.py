from celery import Celery
from celery.schedules import crontab
from config import settings

celery_app = Celery(
    "centraldsp",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "tasks.rules",
        "tasks.sync",
        "tasks.reports",
        "tasks.cleanup",
        "tasks.bidding",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,       # re-queue on worker crash
    worker_prefetch_multiplier=1,
    task_routes={
        "tasks.rules.*":   {"queue": "default"},
        "tasks.sync.*":    {"queue": "sync"},
        "tasks.bidding.*": {"queue": "rtb"},
        "tasks.reports.*": {"queue": "default"},
        "tasks.cleanup.*": {"queue": "default"},
    },
)

# ─── Scheduled tasks (Celery Beat) ───
celery_app.conf.beat_schedule = {
    # AutoPilot: evaluate rules every 30 seconds
    "evaluate-rules": {
        "task": "tasks.rules.evaluate_all_rules",
        "schedule": 30.0,
        "options": {"queue": "default"},
    },
    # Sync ad network costs every 15 minutes
    "sync-ad-costs": {
        "task": "tasks.sync.sync_all_ad_accounts",
        "schedule": 900.0,   # 15 min
        "options": {"queue": "sync"},
    },
    # RTB bid optimizer: recalculate bid prices every 5 minutes
    "optimize-bids": {
        "task": "tasks.bidding.optimize_all_bids",
        "schedule": 300.0,   # 5 min
        "options": {"queue": "rtb"},
    },
    # Daily report at 08:00 UTC
    "daily-report": {
        "task": "tasks.reports.send_daily_report",
        "schedule": crontab(hour=8, minute=0),
        "options": {"queue": "default"},
    },
    # Cleanup expired links every hour
    "cleanup-expired": {
        "task": "tasks.cleanup.cleanup_expired_links",
        "schedule": 3600.0,
        "options": {"queue": "default"},
    },
}
