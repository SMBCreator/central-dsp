"""cronexpr — парсер 5-полевых cron-выражений планировщика (C1b)."""
from datetime import UTC, datetime

import pytest

from cronexpr import CronError, CronExpr, next_run, validate

T = datetime(2026, 9, 11, 12, 34, 56, tzinfo=UTC)  # пятница


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("* * * * *", datetime(2026, 9, 11, 12, 35, tzinfo=UTC)),
        ("*/5 * * * *", datetime(2026, 9, 11, 12, 35, tzinfo=UTC)),
        ("15 * * * *", datetime(2026, 9, 11, 13, 15, tzinfo=UTC)),
        ("30 21 * * *", datetime(2026, 9, 11, 21, 30, tzinfo=UTC)),
        ("0 6 * * *", datetime(2026, 9, 12, 6, 0, tzinfo=UTC)),
        ("0 3 2 * *", datetime(2026, 10, 2, 3, 0, tzinfo=UTC)),
        ("0 9 * * 1-5", datetime(2026, 9, 14, 9, 0, tzinfo=UTC)),  # понедельник
        ("0 0 * * 7", datetime(2026, 9, 13, 0, 0, tzinfo=UTC)),  # 7 = воскресенье
        ("0 0 * * 0", datetime(2026, 9, 13, 0, 0, tzinfo=UTC)),
        ("0 0 29 2 *", datetime(2028, 2, 29, 0, 0, tzinfo=UTC)),
        ("0 0 31 * *", datetime(2026, 10, 31, 0, 0, tzinfo=UTC)),  # сентябрь пропущен
        ("5/10 * * * *", datetime(2026, 9, 11, 12, 35, tzinfo=UTC)),
        ("0,30 8-10 * * *", datetime(2026, 9, 12, 8, 0, tzinfo=UTC)),
    ],
)
def test_next_after(expr, expected):
    assert CronExpr.parse(expr).next_after(T) == expected


def test_next_is_strictly_after():
    exact = datetime(2026, 9, 11, 12, 35, tzinfo=UTC)
    assert CronExpr.parse("35 12 * * *").next_after(exact) == datetime(2026, 9, 12, 12, 35, tzinfo=UTC)


def test_dom_and_dow_both_restricted_is_or():
    # 13 сентября 2026 — воскресенье; ближайшая пятница 13-е далеко, но
    # Vixie-семантика: сработает И 13-го числа, И по пятницам.
    e = CronExpr.parse("0 12 13 * 5")
    assert e.next_after(T) == datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
    assert e.next_after(datetime(2026, 9, 13, 12, 0, tzinfo=UTC)) == datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def test_validate_normalizes_whitespace():
    assert validate("  0   6 * * *  ") == "0 6 * * *"


@pytest.mark.parametrize(
    "bad",
    ["", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * 32 * *", "* * * 13 *",
     "* * * * 8", "*/0 * * * *", "a * * * *", "5-3 * * * *", "@daily", "* * * jan *", "0 0 30 2 *"],
)
def test_invalid(bad):
    with pytest.raises(CronError):
        next_run(bad, T)


def test_naive_datetime_rejected():
    with pytest.raises(CronError):
        CronExpr.parse("* * * * *").next_after(datetime(2026, 1, 1))  # noqa: DTZ001
