"""Audit F-02 / B14c: слой атрибуции конверсий — чистая логика и контракты.

Договор: статус attributed|out_of_window|unattributed (model last_click);
coverage[dim] = attributed с ненулевым измерением / все конверсии владельца;
< 0.8 → confidence=low → авто-стоп/blacklist по CPA/CVR/conversions запрещён;
loss_reason — измерение воронки, конверсий по нему нет.
"""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import conversion_attribution as attribution
import obs_metrics
import stats_math as sm


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


# --- classify ------------------------------------------------------------------------

def test_classify_statuses():
    assert attribution.classify(None, NOW, 30) == "unattributed"
    assert attribution.classify({"created_at": None}, NOW, 30) == "unattributed"
    assert attribution.classify({"created_at": NOW - timedelta(days=29)}, NOW, 30) == "attributed"
    assert attribution.classify({"created_at": NOW - timedelta(days=30)}, NOW, 30) == "attributed"  # ровно окно — внутри
    assert attribution.classify({"created_at": NOW - timedelta(days=30, seconds=1)}, NOW, 30) == "out_of_window"


def test_classify_naive_bid_time_is_utc():
    naive = (NOW - timedelta(days=1)).replace(tzinfo=None)
    assert attribution.classify({"created_at": naive}, NOW, 30) == "attributed"


# --- stats_math ------------------------------------------------------------------------

def test_coverage_ratio_and_confidence():
    assert sm.coverage_ratio(0, 0) is None
    assert sm.coverage_ratio(8, 10) == 0.8
    assert sm.coverage_ratio(12, 10) == 1.0  # защита от рассинхрона счётчиков
    assert sm.coverage_confidence(None) == sm.CONFIDENCE_NA
    assert sm.coverage_confidence(0.79) == sm.CONFIDENCE_LOW
    assert sm.coverage_confidence(0.8) == sm.CONFIDENCE_HIGH


def test_conversion_kpi_trustworthy():
    assert sm.conversion_kpi_trustworthy(0, 0) is True      # конверсий нет — покрытие не определено
    assert sm.conversion_kpi_trustworthy(8, 10) is True
    assert sm.conversion_kpi_trustworthy(7, 10) is False
    assert sm.CONVERSION_KPI_METRICS == {"cpa", "cvr", "conversions"}
    assert sm.ROW_KINDS_WITHOUT_CPA == {"not_set", "unattributed", "out_of_window"}


# --- record(): что уходит в INSERT и в метрики ----------------------------------------------

class _Pool:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "INSERT 0 1"


def test_record_attributed_copies_bid_dims_and_does_not_count_metric():
    obs_metrics.reset_for_tests()
    pool = _Pool()
    bid = {f: f"v_{f}" for f in attribution.BID_DIM_FIELDS}
    bid["created_at"] = NOW - timedelta(hours=1)
    _run(attribution.record(pool, conversion_id="c1", owner_user_id="o", pixel_id="p", goal_id="g",
                            status="attributed", clid="b1", bid=bid, converted_at=NOW,
                            external_conversion_id="ord-7", raw_payload={"type": "event"}, is_test=False))
    sql, args = pool.calls[0]
    assert "INSERT INTO conversion_attributions" in sql and "ON CONFLICT (conversion_id) DO NOTHING" in sql
    assert args[4] == "attributed" and args[5] == "last_click" and args[7] == bid["created_at"]
    assert args[9:22] == tuple(f"v_{f}" for f in attribution.BID_DIM_FIELDS)
    assert args[22] == "ord-7" and '"type": "event"' in args[23]
    assert 'conversions_unattributed_total{status="unattributed"} 0' in obs_metrics.render_prometheus()


def test_record_out_of_window_keeps_dims_null_and_counts_metric():
    obs_metrics.reset_for_tests()
    pool = _Pool()
    bid = {"campaign_id": "camp", "created_at": NOW - timedelta(days=40)}
    _run(attribution.record(pool, conversion_id="c2", owner_user_id="o", pixel_id="p", goal_id="g",
                            status="out_of_window", clid="b2", bid=bid, converted_at=NOW,
                            external_conversion_id=None, raw_payload=None, is_test=False))
    _, args = pool.calls[0]
    assert args[4] == "out_of_window" and args[7] == bid["created_at"]
    assert all(v is None for v in args[9:22])  # измерения бида — только для attributed
    out = obs_metrics.render_prometheus()
    assert 'conversions_unattributed_total{status="out_of_window"} 1' in out
    assert 'conversions_unattributed_total{status="unattributed"} 0' in out


def test_record_test_conversion_not_counted():
    obs_metrics.reset_for_tests()
    _run(attribution.record(_Pool(), conversion_id="c3", owner_user_id="o", pixel_id="p", goal_id="g",
                            status="unattributed", clid=None, bid=None, converted_at=NOW,
                            external_conversion_id=None, raw_payload=None, is_test=True))
    assert 'conversions_unattributed_total{status="unattributed"} 0' in obs_metrics.render_prometheus()


def test_record_rejects_unknown_status():
    with pytest.raises(ValueError):
        _run(attribution.record(_Pool(), conversion_id="c", owner_user_id="o", pixel_id="p", goal_id="g",
                                status="view_through", clid=None, bid=None, converted_at=NOW,
                                external_conversion_id=None, raw_payload=None, is_test=False))


# --- obs_metrics ------------------------------------------------------------------------

def test_coverage_gauge_renders_only_measured_dimensions():
    obs_metrics.reset_for_tests()
    out = obs_metrics.render_prometheus()
    assert "# TYPE conversion_attribution_coverage gauge" in out
    assert "conversion_attribution_coverage{" not in out
    obs_metrics.set_coverage("campaign", 0.75)
    obs_metrics.set_coverage("os", None)
    out = obs_metrics.render_prometheus()
    assert 'conversion_attribution_coverage{dimension="campaign"} 0.75' in out
    assert 'dimension="os"' not in out
    with pytest.raises(KeyError):
        obs_metrics.set_coverage("loss_reason", 1.0)
    with pytest.raises(KeyError):
        obs_metrics.inc_conversion_unattributed("attributed")
    obs_metrics.reset_for_tests()


def test_coverage_dimensions_match_attribution_columns_and_report_dims():
    import routers.stats as st
    for d in obs_metrics.COVERAGE_DIMENSIONS:
        assert d in attribution.DIM_COLUMNS, d
        assert d in st._DIM_EVENTS, d
    assert "loss_reason" not in attribution.DIM_COLUMNS
    # все не-временные события-измерения, кроме loss_reason, дают конверсии
    for d in st._DIM_EVENTS:
        if d in st._TIME_DIMS or d == "loss_reason":
            continue
        assert d in st._DIM_CONV, d
    assert "loss_reason" not in st._DIM_CONV
    assert st._DIM_CONV["os"] == "ca.os" and st._DIM_CONV["publisher"] == "ca.publisher_id"


# --- service rows ------------------------------------------------------------------------

def test_service_conversion_row_shape():
    import routers.stats as st
    row = st._service_conversion_row(sm.ROW_KIND_UNATTRIBUTED, sm.UNATTRIBUTED_LABEL, ["campaign", "device"], 4)
    assert row["kind"] == "unattributed" and row["campaign"] == sm.UNATTRIBUTED_LABEL and row["device"] == ""
    assert row["conversions"] == 4 and row["spend"] == 0.0 and row["bids"] == 0
    assert row["cpa"] is None and row["losses"] is None and row["avg_bid"] is None


# --- migration 045: контракт с кодом -------------------------------------------------

MIG = Path(__file__).resolve().parents[1] / "migrations" / "045_conversion_attributions.sql"


def test_migration_045_shape():
    sql = MIG.read_text()
    assert "CREATE TABLE IF NOT EXISTS conversion_attributions" in sql
    assert "conversion_id          UUID PRIMARY KEY REFERENCES conversions(id) ON DELETE CASCADE" in sql
    assert "CHECK (attribution_status IN ('attributed', 'out_of_window', 'unattributed'))" in sql
    assert "CHECK (attribution_model IN ('last_click'))" in sql
    for col in (*attribution.DIM_COLUMNS.values(), "external_conversion_id", "raw_payload", "owner_user_id",
                "converted_at", "bid_at", "is_test"):
        assert re.search(rf"^\s+{col}\s", sql, flags=re.MULTILINE), col
    assert "ON CONFLICT (conversion_id) DO NOTHING" in sql   # идемпотентный backfill
    # backfill: attributed по campaign_id, out_of_window по найденному биду, иначе unattributed
    assert "WHEN cv.campaign_id IS NOT NULL THEN 'attributed'" in sql
    assert "WHEN cv.clid IS NOT NULL AND be.bid_id IS NOT NULL THEN 'out_of_window'" in sql


# --- rules_engine / optimizer: запрет по недоверенным KPI ----------------------------------

def test_rules_engine_skips_conversion_kpi_rules_when_coverage_low(monkeypatch):
    import rules_engine as re_

    rules = [
        {"id": "r-cpa", "metric": "cpa", "window_hours": 24, "campaign_id": "c1", "name": "cpa",
         "operator": "gt", "threshold": 1, "action": "notify", "action_value": None},
        {"id": "r-ctr", "metric": "ctr", "window_hours": 24, "campaign_id": "c1", "name": "ctr",
         "operator": "gt", "threshold": 0, "action": "notify", "action_value": None},
    ]

    class _Pool:
        async def fetch(self, sql, *a):
            return rules

        async def execute(self, *a):
            return "UPDATE 1"

    async def pool():
        return _Pool()

    async def metrics(pool, cid, wh):
        return {"impressions": 100, "clicks": 5, "spend": 10.0, "conversions": 0, "ctr": 5.0, "cpm": 100.0,
                "cpc": 2.0, "cpa": 0.0, "cvr": 0.0, "win_rate": 50.0, "budget_spent_pct": 0.0}

    async def untrusted(pool, uid, wh):
        return False

    monkeypatch.setattr(re_, "get_pool", pool)
    monkeypatch.setattr(re_, "_campaign_metrics", metrics)
    monkeypatch.setattr(re_.attribution, "kpi_trustworthy_for_owner", untrusted)
    triggered = _run(re_.evaluate_rules_for_user("u"))
    assert [t["rule_id"] for t in triggered] == ["r-ctr"]   # cpa пропущено, ctr сработало


def test_optimizer_has_kpi_guard_on_conversion_rules():
    src = Path(__file__).resolve().parents[1].joinpath("optimizer.py").read_text()
    assert src.count("conv_kpi_ok and") == 2, "правила 1 (fraud CTR без конверсий) и 3 (CPA overrun) должны быть под guard"
    assert "kpi_trustworthy_for_owner" in src
