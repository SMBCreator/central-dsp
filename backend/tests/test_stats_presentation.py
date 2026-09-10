"""Audit F-02: одна арифметика для всех отчётов.

Договор (stats_math):
  * spend в БД — net; в API — net × mult, применяется ровно один раз (present_spend);
  * производные метрики — только derive(), одна точность;
  * totals отчёта не зависят от LIMIT; хвост сворачивается в «Прочее» так, что
    sum(rows) == totals; NULL измерения — «Не указано», а не тире;
  * конверсии в raw-отчёте: из conversions для доступных измерений, иначе None
    (не ноль) + meta.conversions_available=false;
  * date_from > date_to — 422, не молчаливый swap.
"""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

import stats_math as sm
from routers import stats as st

# ---------------------------------------------------------------------------
# stats_math units
# ---------------------------------------------------------------------------


def test_present_spend_applies_multiplier_once_and_rounds():
    assert sm.present_spend(100, 1.3) == 130.0
    assert sm.present_spend("12.34567", 1.0) == 12.3457
    assert sm.present_spend(None, 2.0) == 0.0


def test_derive_metrics_and_aliases():
    d = sm.derive(bids=200, wins=50, impressions=40, clicks=2, viewable_imps=30, spend=8.0, conversions=4, losses=100)
    assert d["win_rate"] == 25.0
    assert d["ctr"] == 5.0
    assert d["cpm"] == d["ecpm"] == 200.0
    assert d["cpc"] == 4.0
    assert d["cpa"] == 2.0
    assert d["viewability"] == 75.0
    assert d["loss_rate"] == 50.0


def test_derive_distinguishes_unknown_conversions_from_zero():
    assert sm.derive(impressions=10, spend=5.0, conversions=None)["cpa"] is None
    assert sm.derive(impressions=10, spend=5.0, conversions=0)["cpa"] == 0.0
    assert "loss_rate" not in sm.derive(bids=10)          # hourly-источник не отдаёт losses
    assert sm.derive(bids=0, impressions=0)["ctr"] == 0.0  # деление на ноль → 0.0, не исключение


def test_clamps():
    assert sm.clamp_hours(-5) == 1
    assert sm.clamp_hours(10**6) == sm.MAX_HOURS
    assert sm.clamp_hours("abc") == 24
    assert sm.clamp_limit(0) == 1
    assert sm.clamp_limit(99999) == sm.MAX_LIMIT
    assert sm.clamp_limit(None, 100) == 100


@pytest.mark.parametrize("frm,to,msg", [
    ("2026-01-10", "2026-01-01", "позже"),
    ("2026-01-01", None, "обе даты"),
    ("2026/01/01", "2026-01-02", "YYYY-MM-DD"),
    ("2025-01-01", "2026-01-03", "года"),
])
def test_parse_date_range_rejects(frm, to, msg):
    with pytest.raises(sm.DateRangeError) as ei:
        sm.parse_date_range(frm, to)
    assert msg in str(ei.value)


def test_parse_date_range_ok():
    d_from, d_to = sm.parse_date_range("2026-01-01", "2026-01-31")
    assert (d_to - d_from).days == 30


# ---------------------------------------------------------------------------
# report(): contract on a fake pool
# ---------------------------------------------------------------------------

CAMP_A, CAMP_B = str(uuid4()), str(uuid4())
TS = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _rec(**kw):
    base = {"bids": 0, "wins": 0, "impressions": 0, "clicks": 0, "viewable_imps": 0, "spend": 0,
            "conversions": None, "losses": 0, "avg_bid": None, "avg_bidfloor": None, "avg_min_to_win": None}
    base.update(kw)
    return base


class _Conn:
    """Роутит запросы по форме SQL; хранит groups/totals/conversions сценария."""

    def __init__(self, scenario):
        self.s = scenario
        self.queries = []

    async def fetch(self, q, *args):
        self.queries.append(q)
        if "FROM campaigns" in q and "SELECT id FROM" in q:
            return [{"id": CAMP_A}, {"id": CAMP_B}]
        if "SELECT id, name FROM" in q:
            return [{"id": CAMP_A, "name": "Alpha"}, {"id": CAMP_B, "name": "Beta"}]
        if "FROM conversions cv" in q:
            return self.s.get("conv_groups", [])
        if "GROUP BY" in q:
            assert "LIMIT" in q
            return self.s["groups"]
        raise AssertionError(q)

    async def fetchrow(self, q, *args):
        self.queries.append(q)
        assert "GROUP BY" not in q and "LIMIT" not in q, "totals must not be grouped or limited"
        return self.s["totals"]

    async def fetchval(self, q, *args):
        self.queries.append(q)
        assert "FROM conversions cv" in q
        return self.s.get("conv_total", 0)


class _Pool:
    def __init__(self, scenario):
        self.conn = _Conn(scenario)

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _Ctx()


def _run_report(monkeypatch, scenario, body, mult=1.0):
    pool = _Pool(scenario)

    async def fake_get_pool():
        return pool

    async def fake_scope(user):
        return [str(uuid4())], None, mult

    monkeypatch.setattr(st, "get_pool", fake_get_pool)
    monkeypatch.setattr(st, "_resolve_scope", fake_scope)
    req = st.ReportRequest(**body)
    return asyncio.run(st.report(req, user={"id": "u", "role": "agency"})), pool.conn


def test_report_applies_markup_once_to_rows_totals_and_prices(monkeypatch):
    scenario = {
        "groups": [
            {"dim_0": CAMP_A, **_rec(bids=10, wins=5, impressions=4, clicks=1, spend=100, avg_bid=2.0, avg_min_to_win=1.5)},
        ],
        "totals": _rec(bids=10, wins=5, impressions=4, clicks=1, spend=100, avg_bid=2.0, avg_min_to_win=1.5),
        "conv_groups": [{"dim_0": CAMP_A, "conversions": 2}],
        "conv_total": 2,
    }
    body = {"date_from": "2026-09-01", "date_to": "2026-09-02", "groupings": ["campaign"], "countries": ["RU"]}
    out, _ = _run_report(monkeypatch, scenario, body, mult=1.3)
    row, tot = out["rows"][0], out["totals"]
    assert row["campaign"] == "Alpha" and row["kind"] == "data"
    assert row["spend"] == tot["spend"] == 130.0
    assert row["avg_bid"] == 2.6 and row["avg_min_to_win"] == 1.95
    assert row["cpm"] == tot["cpm"] == 32500.0            # 130 / 4 * 1000 — от marked-up spend
    assert row["conversions"] == tot["conversions"] == 2
    assert row["cpa"] == 65.0
    assert out["meta"] == {"source": "events", "truncated": False, "limit": 1000, "conversions_available": True}


def test_report_hourly_source_uses_stats_conversions_and_markup(monkeypatch):
    scenario = {
        # hourly-SQL отдаёт losses NULL (см. metrics_sql для stats_hourly)
        "groups": [{"dim_0": TS, **_rec(bids=3, wins=1, impressions=1, clicks=0, spend=10, conversions=1, losses=None)}],
        "totals": _rec(bids=3, wins=1, impressions=1, clicks=0, spend=10, conversions=1, losses=None),
    }
    body = {"date_from": "2026-09-01", "date_to": "2026-09-02", "groupings": ["day"]}
    out, conn = _run_report(monkeypatch, scenario, body, mult=2.0)
    assert out["meta"]["source"] == "hourly"
    assert out["rows"][0]["day"] == TS.isoformat()
    assert out["rows"][0]["spend"] == 20.0 and out["totals"]["spend"] == 20.0
    assert out["rows"][0]["conversions"] == 1 and out["rows"][0]["cpa"] == 20.0
    assert out["rows"][0]["losses"] is None and "loss_rate" not in out["rows"][0]   # hourly не измеряет lurl
    assert out["totals"]["losses"] is None
    assert not any("FROM conversions" in q for q in conn.queries)


def test_report_truncation_folds_tail_into_other_so_rows_sum_to_totals(monkeypatch):
    groups = [
        {"dim_0": "chrome", **_rec(bids=10, impressions=10, clicks=2, spend=10)},
        {"dim_0": "safari", **_rec(bids=8, impressions=8, clicks=1, spend=8)},
        {"dim_0": "firefox", **_rec(bids=1, impressions=1, clicks=0, spend=1)},   # limit+1-й ряд
    ]
    scenario = {"groups": groups, "totals": _rec(bids=25, impressions=25, clicks=5, spend=25, losses=4)}
    body = {"date_from": "2026-09-01", "date_to": "2026-09-02", "groupings": ["browser"], "limit": 2}
    out, _ = _run_report(monkeypatch, scenario, body)
    rows = out["rows"]
    assert out["meta"]["truncated"] is True and out["meta"]["limit"] == 2
    assert [r["browser"] for r in rows] == ["chrome", "safari", sm.OTHER_LABEL]
    other = rows[-1]
    assert other["kind"] == "other"
    assert other["bids"] == 7 and other["impressions"] == 7 and other["clicks"] == 2 and other["spend"] == 7.0
    assert other["losses"] == 4 and other["avg_bid"] is None
    for k in ("bids", "impressions", "clicks", "spend", "losses"):
        assert sum(r[k] for r in rows) == out["totals"][k], k
    # browser нет в conversions → конверсии недоступны, не ноль
    assert out["meta"]["conversions_available"] is False
    assert all(r["conversions"] is None and r["cpa"] is None for r in rows)
    assert out["totals"]["conversions"] is None


def test_report_null_dimension_is_not_set_row(monkeypatch):
    scenario = {
        "groups": [
            {"dim_0": "RU", "dim_1": None, **_rec(impressions=5, spend=5)},
            {"dim_0": None, "dim_1": "mobile", **_rec(impressions=1, spend=1)},
        ],
        "totals": _rec(impressions=6, spend=6),
        "conv_groups": [{"dim_0": "RU", "dim_1": None, "conversions": 3}],
        "conv_total": 3,
    }
    body = {"date_from": "2026-09-01", "date_to": "2026-09-02", "groupings": ["country", "device"]}
    out, _ = _run_report(monkeypatch, scenario, body)
    r0, r1 = out["rows"]
    assert r0["country"] == "RU" and r0["device"] == sm.NOT_SET_LABEL and r0["kind"] == "not_set"
    assert r0["conversions"] == 3                      # конверсии с NULL-измерением сохраняются в строке «Не указано»
    assert r1["country"] == sm.NOT_SET_LABEL and r1["conversions"] == 0
    assert out["totals"]["conversions"] == 3


def test_report_rejects_reversed_dates_instead_of_swapping(monkeypatch):
    scenario = {"groups": [], "totals": _rec()}
    body = {"date_from": "2026-09-10", "date_to": "2026-09-01", "groupings": ["day"]}
    with pytest.raises(HTTPException) as ei:
        _run_report(monkeypatch, scenario, body)
    assert ei.value.status_code == 422 and "позже" in ei.value.detail


def test_report_limit_is_clamped_and_time_dims_keep_chronological_order(monkeypatch):
    scenario = {"groups": [], "totals": _rec()}
    body = {"date_from": "2026-09-01", "date_to": "2026-09-02", "groupings": ["hour"], "limit": 10**9}
    out, conn = _run_report(monkeypatch, scenario, body)
    assert out["meta"]["limit"] == sm.MAX_LIMIT
    grouped = [q for q in conn.queries if "GROUP BY" in q and "LIMIT" in q][0]
    assert "ORDER BY dim_0 LIMIT" in grouped.replace("\n", " ").replace("  ", " ") or "ORDER BY dim_0" in grouped
    assert f"LIMIT {sm.MAX_LIMIT + 1}" in grouped


def test_report_non_time_dims_order_by_spend_so_tail_is_small(monkeypatch):
    scenario = {"groups": [], "totals": _rec()}
    body = {"date_from": "2026-09-01", "date_to": "2026-09-02", "groupings": ["campaign", "day"]}
    _, conn = _run_report(monkeypatch, scenario, body)
    grouped = [q for q in conn.queries if "GROUP BY" in q and "LIMIT" in q][0]
    assert "ORDER BY spend DESC, impressions DESC, bids DESC" in grouped


# ---------------------------------------------------------------------------
# Every spend surface goes through present_spend (no raw round(...*mult) left)
# ---------------------------------------------------------------------------


def test_no_ad_hoc_spend_math_left_in_report_routers():
    import inspect
    import re

    from routers import share, supply_partners, scoring

    for mod in (st, share, supply_partners, scoring):
        src = inspect.getsource(mod)
        assert not re.search(r"round\(float\(r\[.spend.\]\)", src), mod.__name__
        assert not re.search(r"\* 1000, [124]\)", src), f"{mod.__name__}: ecpm/cpm must come from derive()"
        assert not re.search(r"\* 100, [123]\) if", src), f"{mod.__name__}: rates must come from derive()"
