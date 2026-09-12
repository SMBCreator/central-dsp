"""C4b / N-02: технические алерты без БД — парсинг payload Alertmanager,
настройки получателей, auth webhook (Bearer/503 в production), Watchdog ->
gauge без записи, письма/in-app через фейковый пул; контракт compose <->
alertmanager.yml <-> prometheus.yml; правила Watchdog/AlertPipelineDead."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import alerting  # noqa: E402
import obs_metrics  # noqa: E402
from routers import alerts as alerts_router  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
AM_CONFIG = ROOT / "ops" / "alertmanager" / "alertmanager.yml"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("ALERT_WEBHOOK_SECRET", "APP_ENV", "SMTP_HOST", "SMTP_FROM"):
        monkeypatch.delenv(k, raising=False)
    obs_metrics.reset_for_tests()
    yield
    obs_metrics.reset_for_tests()


def _alert(name="BackendNotReady", status="firing", severity="critical", fp="abc123", starts="2026-09-12T10:00:00.123456789Z", **labels):
    return {
        "status": status,
        "labels": {"alertname": name, "severity": severity, "job": "backend", **labels},
        "annotations": {"summary": f"{name} summary", "description": "do X. Runbook: rtb_alerts.md#x"},
        "startsAt": starts,
        "endsAt": "0001-01-01T00:00:00Z" if status == "firing" else "2026-09-12T10:30:00Z",
        "fingerprint": fp,
    }


def _payload(*alerts):
    return {"version": "4", "status": "firing", "externalURL": "http://alertmanager:9093", "alerts": list(alerts)}


# ---- parsing --------------------------------------------------------------------

def test_parse_alert_handles_nanoseconds_and_zero_end():
    a = alerting.parse_alert(_alert(), "http://am")
    assert a["starts_at"] == datetime(2026, 9, 12, 10, 0, 0, 123456, tzinfo=timezone.utc)
    assert a["ends_at"] is None and a["status"] == "firing" and a["severity"] == "critical"
    assert a["labels"]["job"] == "backend" and a["external_url"] == "http://am"


def test_parse_alert_rejects_garbage_and_defaults_severity():
    assert alerting.parse_alert({}, None) is None
    assert alerting.parse_alert({"labels": {"alertname": "X"}, "fingerprint": "f", "status": "firing"}, None) is None  # no startsAt
    a = alerting.parse_alert(_alert(severity="weird"), None)
    assert a["severity"] == "warning"


def test_render_email_subject_and_bodies():
    subject, text, html_body = alerting.render_email(alerting.parse_alert(_alert(), None))
    assert subject.startswith("FIRING [critical] BackendNotReady: BackendNotReady summary")
    assert "severity=critical" in text and "Метки: job=backend" in text
    assert "<strong>BackendNotReady</strong>" in html_body and "Runbook" in html_body
    subject_r, text_r, _ = alerting.render_email(alerting.parse_alert(_alert(status="resolved"), None))
    assert subject_r.startswith("RESOLVED") and "Завершилось: 2026-09-12 10:30:00 UTC" in text_r


# ---- settings -----------------------------------------------------------------------

def test_normalise_settings_validates_and_dedups():
    s = alerting.normalise_settings({"emails": [" Ops@Example.com ", "ops@example.com", ""], "email_min_severity": "CRITICAL", "junk": 1})
    assert s == {"emails": ["ops@example.com"], "email_min_severity": "critical", "email_on_resolve": True}
    assert alerting.normalise_settings(None) == alerting.DEFAULT_SETTINGS
    with pytest.raises(ValueError):
        alerting.normalise_settings({"emails": ["not-an-email"]})
    with pytest.raises(ValueError):
        alerting.normalise_settings({"email_min_severity": "fatal"})
    with pytest.raises(ValueError):
        alerting.normalise_settings({"emails": [f"u{i}@x.io" for i in range(21)]})


# ---- fake pool -----------------------------------------------------------------------

class _Pool:
    """asyncpg-подобный пул: platform_settings, users, alert_events (дедуп), notifications, outbox."""

    def __init__(self, settings=None, superadmins=(("u1", "root@example.com"),)):
        self.settings = settings
        self.superadmins = list(superadmins)
        self.events: set[tuple] = set()
        self.notifications: list[tuple] = []
        self.outbox: list[dict] = []

    async def fetchval(self, sql, *a):
        assert "platform_settings" in sql
        return json.dumps(self.settings) if self.settings is not None else None

    async def fetch(self, sql, *a):
        assert "FROM users" in sql
        return [{"id": i, "email": e} for i, e in self.superadmins]

    async def fetchrow(self, sql, *a):
        if "INSERT INTO alert_events" in sql:
            key = (a[0], a[3], a[4])  # fingerprint, status, starts_at
            if key in self.events:
                return None
            self.events.add(key)
            return {"id": len(self.events)}
        if "INSERT INTO notification_outbox" in sql:
            key = (a[0], a[5])
            if any((o["recipient"], o["dedup_key"]) == key for o in self.outbox):
                return None
            self.outbox.append({"recipient": a[0], "kind": a[1], "subject": a[2], "dedup_key": a[5]})
            return {"id": len(self.outbox)}
        raise AssertionError(sql)

    async def execute(self, sql, *a):
        assert "INSERT INTO notifications" in sql
        self.notifications.append(a)
        return "INSERT 0 1"


def _client(pool, monkeypatch):
    async def _get_pool():
        return pool

    monkeypatch.setattr(alerts_router, "get_pool", _get_pool)
    app = FastAPI()
    app.include_router(alerts_router.router)
    return TestClient(app)


# ---- webhook auth ----------------------------------------------------------------------

def test_webhook_requires_bearer_when_secret_set(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_SECRET", "s3cret-s3cret-s3cret")
    c = _client(_Pool(), monkeypatch)
    assert c.post("/alerts/webhook", json=_payload(_alert())).status_code == 401
    assert c.post("/alerts/webhook", json=_payload(_alert()), headers={"Authorization": "Bearer nope"}).status_code == 401
    r = c.post("/alerts/webhook", json=_payload(_alert()), headers={"Authorization": "Bearer s3cret-s3cret-s3cret"})
    assert r.status_code == 200 and r.json()["stored"] == 1


def test_webhook_closed_in_production_without_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    c = _client(_Pool(), monkeypatch)
    assert c.post("/alerts/webhook", json=_payload(_alert())).status_code == 503


def test_webhook_open_in_dev_without_secret_and_validates_shape(monkeypatch):
    c = _client(_Pool(), monkeypatch)
    assert c.post("/alerts/webhook", json={"alerts": "no"}).status_code == 400
    assert c.post("/alerts/webhook", content=b"{", headers={"content-type": "application/json"}).status_code == 400
    assert c.post("/alerts/webhook", json=_payload(_alert())).status_code == 200


# ---- processing ------------------------------------------------------------------------

def test_firing_creates_in_app_and_email_once_then_dedups(monkeypatch):
    pool = _Pool(settings={"emails": ["ops@example.com", "noc@example.com"]}, superadmins=[("u1", "root@x.io"), ("u2", "root2@x.io")])
    c = _client(pool, monkeypatch)
    r1 = c.post("/alerts/webhook", json=_payload(_alert())).json()
    assert r1["stored"] == 1 and r1["in_app"] == 2 and r1["emails_queued"] == 2
    assert [n[0] for n in pool.notifications] == ["u1", "u2"]
    assert pool.notifications[0][1] == "tech_alert" and pool.notifications[0][4] == alerting.IN_APP_LINK
    assert sorted(o["recipient"] for o in pool.outbox) == ["noc@example.com", "ops@example.com"]
    assert pool.outbox[0]["dedup_key"].startswith("alert:abc123:firing:")
    # repeat_interval: тот же fingerprint/status/startsAt — ничего нового.
    r2 = c.post("/alerts/webhook", json=_payload(_alert())).json()
    assert r2["duplicates"] == 1 and r2["stored"] == 0 and r2["in_app"] == 0 and r2["emails_queued"] == 0
    assert len(pool.notifications) == 2 and len(pool.outbox) == 2
    # resolved — новый переход.
    r3 = c.post("/alerts/webhook", json=_payload(_alert(status="resolved"))).json()
    assert r3["stored"] == 1 and r3["emails_queued"] == 2
    assert pool.outbox[-1]["subject"].startswith("RESOLVED")
    snap = obs_metrics.snapshot()["counters"]
    assert snap["alert_webhook_stored_total"] == 2 and snap["alert_webhook_duplicate_total"] == 1


def test_recipients_fall_back_to_superadmin_emails(monkeypatch):
    pool = _Pool(settings=None, superadmins=[("u1", "root@example.com"), ("u2", None)])
    c = _client(pool, monkeypatch)
    r = c.post("/alerts/webhook", json=_payload(_alert())).json()
    assert r["emails_queued"] == 1 and pool.outbox[0]["recipient"] == "root@example.com"
    assert r["in_app"] == 2  # in-app — всем суперадминам, даже без email


def test_email_severity_threshold_and_resolve_flag(monkeypatch):
    pool = _Pool(settings={"emails": ["ops@example.com"], "email_min_severity": "critical", "email_on_resolve": False})
    c = _client(pool, monkeypatch)
    assert c.post("/alerts/webhook", json=_payload(_alert(severity="warning", fp="w1"))).json()["emails_queued"] == 0
    assert c.post("/alerts/webhook", json=_payload(_alert(severity="critical", fp="c1"))).json()["emails_queued"] == 1
    assert c.post("/alerts/webhook", json=_payload(_alert(severity="critical", fp="c1", status="resolved"))).json()["emails_queued"] == 0
    assert len(pool.notifications) == 3  # in-app не фильтруется порогом


def test_watchdog_updates_gauge_without_storing(monkeypatch):
    pool = _Pool()
    c = _client(pool, monkeypatch)
    before = time.time()
    r = c.post("/alerts/webhook", json=_payload(_alert(name="Watchdog", severity="none", fp="wd"))).json()
    assert r == {"received": 1, "stored": 0, "duplicates": 0, "invalid": 0, "watchdog": 1, "in_app": 0, "emails_queued": 0}
    assert pool.events == set() and pool.notifications == [] and pool.outbox == []
    assert alerting.watchdog_last_received() >= before
    assert obs_metrics.snapshot()["counters"]["alert_webhook_watchdog_total"] == 1


def test_invalid_alerts_counted_not_fatal(monkeypatch):
    pool = _Pool()
    c = _client(pool, monkeypatch)
    r = c.post("/alerts/webhook", json=_payload({"labels": {}}, _alert())).json()
    assert r["invalid"] == 1 and r["stored"] == 1


def test_seed_watchdog_makes_pipeline_healthy_at_start():
    alerting.seed_watchdog()
    assert time.time() - alerting.watchdog_last_received() < 5


# ---- wiring / contracts --------------------------------------------------------------------

def test_webhook_is_exempt_from_csrf_and_audit_and_classified_in_matrix():
    import audit
    import csrf
    from scripts import rbac_matrix

    assert csrf.is_exempt("/alerts/webhook")
    assert not csrf.is_exempt("/alerts/events")
    assert "/alerts/webhook".startswith(audit.SKIP_PREFIXES)
    assert "/alerts/webhook" in rbac_matrix.NO_COOKIE_AUTH_PREFIX


def test_alertmanager_config_points_at_backend_webhook_with_bearer_file():
    cfg = yaml.safe_load(AM_CONFIG.read_text(encoding="utf-8"))
    receivers = {r["name"]: r for r in cfg["receivers"]}
    assert cfg["route"]["receiver"] in receivers
    wh = receivers[cfg["route"]["receiver"]]["webhook_configs"][0]
    assert wh["url"] == "http://backend:8000/alerts/webhook"
    assert wh["send_resolved"] is True
    assert wh["http_config"]["authorization"]["type"] == "Bearer"
    token_file = wh["http_config"]["authorization"]["credentials_file"]
    # Watchdog маршрут — частый repeat, чтобы AlertPipelineDead (1h) имел смысл.
    wd = next(r for r in cfg["route"]["routes"] if any("Watchdog" in m for m in r["matchers"]))
    assert wd["repeat_interval"] == "10m"

    compose = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))
    am = compose["services"]["alertmanager"]
    assert am.get("profiles") == ["monitoring"]
    assert "ports" not in am, "9093 не публикуется"
    assert "ALERT_WEBHOOK_SECRET" in am["environment"]
    assert token_file in " ".join(am["command"]), "compose должен записывать токен в файл, который читает Alertmanager"
    assert any(v.startswith("./ops/alertmanager/alertmanager.yml:") for v in am["volumes"])
    assert "alertmanager_data" in compose["volumes"]
    # backend получает тот же секрет.
    assert "ALERT_WEBHOOK_SECRET" in compose["services"]["backend"]["environment"]

    prom = yaml.safe_load((ROOT / "ops" / "prometheus" / "prometheus.yml").read_text(encoding="utf-8"))
    targets = [t for am_ in prom["alerting"]["alertmanagers"] for sc in am_["static_configs"] for t in sc["targets"]]
    assert targets == ["alertmanager:9093"]


def test_watchdog_and_pipeline_rules_are_covered():
    doc = yaml.safe_load((ROOT / "ops" / "prometheus" / "alerts.yml").read_text(encoding="utf-8"))
    group = next(g for g in doc["groups"] if g["name"] == "centraldsp-notifications")
    rules = {r["alert"]: r for r in group["rules"]}
    assert rules["Watchdog"]["expr"] == "vector(1)" and rules["Watchdog"]["labels"]["severity"] == "none"
    assert "alert_watchdog_last_received_ts" in rules["AlertPipelineDead"]["expr"]
    assert rules["AlertPipelineDead"]["labels"]["severity"] == "critical"
    tests = yaml.safe_load((ROOT / "ops" / "prometheus" / "alerts_test.yml").read_text(encoding="utf-8"))
    tested = {
        t["expr"].split('alertname="')[1].split('"')[0]
        for case in tests["tests"] for t in case.get("promql_expr_test", []) if 'alertname="' in t["expr"]
    }
    assert {"Watchdog", "AlertPipelineDead"} <= tested


def test_ci_compose_step_covers_alertmanager():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "grep -qx alertmanager /tmp/mon.services" in ci
    assert "prometheus|node-exporter|alertmanager" in ci
