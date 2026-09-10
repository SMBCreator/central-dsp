"""Контракт алертов (D8): каждая метрика из ops/prometheus/alerts.yml реально
экспортируется (backend obs_metrics или Go internal/metrics), каждый анкор
runbook существует, у каждого правила есть severity и summary."""

import re
from pathlib import Path

import pytest
import yaml

import obs_metrics

ROOT = Path(__file__).resolve().parents[2]
ALERTS = ROOT / "ops" / "prometheus" / "alerts.yml"
RUNBOOK = ROOT / "docs" / "runbooks" / "rtb_alerts.md"
GO_SRC = ROOT / "bidder-go"

_METRIC_RE = re.compile(r"\b([a-z][a-z0-9_]*_(?:total|seconds|seconds_bucket|rows|up|sum|count))\b")
_PROMQL_FUNCS = {"rate", "increase", "sum", "clamp_min", "time", "histogram_quantile", "absent", "avg", "max", "min"}


def _rules():
    doc = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    return [r for g in doc["groups"] for r in g["rules"] if "alert" in r]


def _backend_metric_names() -> set[str]:
    obs_metrics.reset_for_tests()
    for g in ("bid_events_default_partition_rows", "ord_export_last_success_timestamp_seconds",
              "rtb_pending_burl_size", "rtb_pending_burl_oldest_age_seconds"):
        obs_metrics.set_gauge(g, 1)
    obs_metrics.inc_burl_outcome("seed", "won")  # R-07: серия появляется только с данными
    for job in obs_metrics.CRON_JOBS:
        obs_metrics.record_cron_run(job, ok=True)
    names = set()
    for line in obs_metrics.render_prometheus().splitlines():
        if line.startswith("#") or not line:
            continue
        names.add(line.split("{")[0].split(" ")[0])
    obs_metrics.reset_for_tests()
    return names


def _go_metric_names() -> set[str]:
    """Имена серий Go: счётчики/гистограммы в internal/metrics, gauges
    регистрируются в cmd/bidder/main.go (RegisterGauge) — сканируем всё."""
    names: set[str] = set()
    for f in GO_SRC.rglob("*.go"):
        if f.name.endswith("_test.go"):
            continue
        names |= set(re.findall(r"\bbidder_[a-z0-9_]+\b", f.read_text(encoding="utf-8")))
    return names


def test_alert_rules_reference_existing_metrics():
    known = _backend_metric_names() | _go_metric_names()
    # Гистограммы рендерятся как _bucket/_sum/_count; базовое имя тоже допустимо.
    known |= {n.rsplit("_", 1)[0] for n in known if n.endswith(("_bucket", "_sum", "_count"))}
    missing = {}
    for r in _rules():
        for m in _METRIC_RE.findall(r["expr"]):
            if m in _PROMQL_FUNCS:
                continue
            if m not in known:
                missing.setdefault(r["alert"], set()).add(m)
    assert not missing, f"alerts reference unknown metrics: {missing}"


def test_every_rule_has_severity_summary_and_runbook_anchor():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    anchors = set(re.findall(r"^## ([a-z0-9-]+) — ", runbook, flags=re.M))
    for r in _rules():
        assert r["labels"].get("severity") in ("warning", "critical"), r["alert"]
        assert r["annotations"].get("summary"), r["alert"]
        m = re.search(r"rtb_alerts\.md#([a-z0-9-]+)", r["annotations"].get("description", ""))
        assert m, f"{r['alert']} has no runbook link"
        assert m.group(1) in anchors, f"{r['alert']}: runbook anchor #{m.group(1)} missing (have {sorted(anchors)})"


@pytest.mark.parametrize(
    "alert",
    ["BidderGateErrorRate", "Backend5xxShare", "BidEventsDefaultPartitionNonEmpty", "OrdExportLag", "OrdExportFailed"],
)
def test_d8_alerts_present(alert):
    assert alert in {r["alert"] for r in _rules()}
