"""Business day boundary for daily budgets (audit M-04).

The platform sells to advertisers in the Russian Federation, so a "day" for
daily budgets, `spent_today` and the Redis `spend:*` counters is the calendar
day in Europe/Moscow. Every component that touches a daily counter derives
the date and the TTL from this module — Python here, `redisx.BusinessDate`
on the Go side — so there is exactly one reset per day, at 00:00 MSK.

Redis keys carry the business date in the name (`spend:camp:{id}:{YYYY-MM-DD}`)
instead of relying on a cron job to delete them: a late or repeated cron can
no longer wipe the current day's spend, and a loss notice that arrives after
midnight refunds yesterday's key, never today's.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BUSINESS_TZ_NAME = os.environ.get("BUSINESS_TZ", "Europe/Moscow")

try:
    BUSINESS_TZ = ZoneInfo(BUSINESS_TZ_NAME)
except ZoneInfoNotFoundError:  # minimal images without tzdata
    BUSINESS_TZ = timezone(timedelta(hours=3), name="MSK")

# Keep yesterday's counter around for a while after midnight so that
# reconciliation and late loss notices still find it. Mirrors Go.
KEY_GRACE_SECONDS = 3600


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def business_date(now: datetime | None = None) -> date:
    """Calendar date of `now` (default: current time) in the business timezone."""
    now = now or now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(BUSINESS_TZ).date()


def business_date_str(now: datetime | None = None) -> str:
    return business_date(now).isoformat()


def next_business_midnight(now: datetime | None = None) -> datetime:
    """UTC instant of the next 00:00 in the business timezone."""
    now = now or now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(BUSINESS_TZ)
    next_day = (local + timedelta(days=1)).date()
    midnight = datetime.combine(next_day, datetime.min.time(), tzinfo=BUSINESS_TZ)
    return midnight.astimezone(timezone.utc)


def seconds_until_business_midnight(now: datetime | None = None, *, grace: int = KEY_GRACE_SECONDS) -> int:
    """TTL for a daily counter created now: until the next business midnight
    plus `grace` seconds. Never less than 60 s so a key created a moment
    before midnight is still usable by the request that created it."""
    now = now or now_utc()
    remaining = int((next_business_midnight(now) - now).total_seconds())
    return max(remaining, 60) + max(grace, 0)


def spend_key(scope: str, entity_id: str, day: str | date | None = None) -> str:
    """Redis key of a daily spend counter. scope: 'camp' | 'li'."""
    if day is None:
        day = business_date_str()
    elif isinstance(day, date):
        day = day.isoformat()
    return f"spend:{scope}:{entity_id}:{day}"


def normalize_spent_today(row: dict, now: datetime | None = None) -> dict:
    """Zero `spent_today` in a campaigns/line_items dict when its
    `spent_today_date` is not the current business day. Mutates and returns
    `row`; drops the internal date column from API output."""
    if "spent_today" in row:
        counter_day = row.get("spent_today_date")
        if counter_day is not None and counter_day != business_date(now):
            row["spent_today"] = 0
    row.pop("spent_today_date", None)
    return row


def spend_key_date(key: str) -> str | None:
    """Business date suffix of a `spend:*` key, or None for legacy undated keys."""
    parts = key.split(":")
    if len(parts) != 4:
        return None
    suffix = parts[3]
    try:
        date.fromisoformat(suffix)
    except ValueError:
        return None
    return suffix
