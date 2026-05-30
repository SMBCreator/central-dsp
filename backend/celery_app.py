import os
from celery import Celery
from celery.schedules import crontab

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

app = Celery(
    "centraldsp",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "tasks.rules",
        "tasks.sync",
        "tasks.reports",
        "tasks.cleanup",
        "tasks.bidding",
    ],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,          # re-queue on worker crash
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # fair distribution
    task_routes={
        "tasks.rules.*":   {"queue": "default"},
        "tasks.sync.*":    {"queue": "sync"},
        "tasks.bidding.*": {"queue": "rtb"},
        "tasks.reports.*": {"queue": "default"},
        "tasks.cleanup.*": {"queue": "default"},
    },
)

# ─────────────────────────────────────────
# Periodic tasks schedule
# ─────────────────────────────────────────
app.conf.beat_schedule = {
    # AutoPilot rule evaluation — every 30 seconds
    "evaluate-rules": {
        "task": "tasks.rules.evaluate_all_rules",
        "schedule": 30.0,
        "options": {"queue": "default"},
    },
    # Sync ad network costs — every 15 minutes
    "sync-ad-costs": {
        "task": "tasks.sync.sync_all_ad_networks",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "sync"},
    },
    # RTB bid optimizer — every 5 minutes
    "optimize-bids": {
        "task": "tasks.bidding.optimize_all_bids",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "rtb"},
    },
    # Daily report — every day at 08:00 UTC
    "daily-report": {
        "task": "tasks.reports.send_daily_report",
        "schedule": crontab(hour=8, minute=0),
        "options": {"queue": "default"},
    },
    # Cleanup expired links — every hour
    "cleanup-expired": {
        "task": "tasks.cleanup.cleanup_expired_links",
        "schedule": crontab(minute=0),
        "options": {"queue": "default"},
    },
}
