"""Celery Beat periodic task schedule for Central DSP.

Import this in celery_app.py:
    from celery_beat_schedule import BEAT_SCHEDULE
    app.conf.beat_schedule = BEAT_SCHEDULE
"""
from celery.schedules import crontab

BEAT_SCHEDULE = {
    # ── Stats aggregation: every hour at :00 ──────────────────────────────────
    "aggregate-campaign-stats-hourly": {
        "task": "tasks.stats.aggregate_campaign_stats",
        "schedule": crontab(minute=0),          # every hour at XX:00
        "options": {"queue": "default"},
    },

    # ── AutoPilot rules: every 5 minutes ─────────────────────────────────────
    "evaluate-autopilot-rules": {
        "task": "tasks.autopilot.evaluate_rules",
        "schedule": crontab(minute="*/5"),       # every 5 min
        "options": {"queue": "default"},
    },

    # ── Reset daily budgets: midnight UTC every day ───────────────────────────
    "reset-daily-budgets-midnight": {
        "task": "tasks.budget.reset_daily_budgets",
        "schedule": crontab(hour=0, minute=0),   # 00:00 UTC
        "options": {"queue": "default"},
    },

    # ── Bid optimizer: every 5 minutes ───────────────────────────────────────
    "optimize-bids": {
        "task": "tasks.bidding.optimize_all_campaigns",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "default"},
    },
}

# Timezone for beat scheduler
TIMEZONE = "UTC"
