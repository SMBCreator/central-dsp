"""C4b / N-01: ядро доставки уведомлений без БД — конфиг SMTP из env, backoff,
сборка письма, dispatch над подставным пулом (SMTP подменён), no-op без SMTP,
инварианты: job в CRON_JOBS/DEFAULT_JOBS/роут, правило алерта покрыто тестом."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notify_outbox  # noqa: E402
import obs_metrics  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_STARTTLS", "SMTP_SSL"):
        monkeypatch.delenv(k, raising=False)
    obs_metrics.reset_for_tests()
    yield
    obs_metrics.reset_for_tests()


# ---- config ------------------------------------------------------------------

def test_not_configured_without_host_or_from(monkeypatch):
    assert not notify_outbox.is_configured()
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    assert not notify_outbox.is_configured()  # FROM обязателен
    monkeypatch.setenv("SMTP_FROM", "alerts@example.com")
    assert notify_outbox.is_configured()


def test_config_defaults_and_flags(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "h")
    monkeypatch.setenv("SMTP_FROM", "a@b")
    cfg = notify_outbox.smtp_config()
    assert cfg.port == 587 and cfg.starttls is True and cfg.ssl is False
    monkeypatch.setenv("SMTP_STARTTLS", "0")
    monkeypatch.setenv("SMTP_SSL", "1")
    monkeypatch.setenv("SMTP_PORT", "465")
    cfg = notify_outbox.smtp_config()
    assert cfg.port == 465 and cfg.starttls is False and cfg.ssl is True


def test_backoff_schedule_is_monotonic_and_capped():
    seq = [notify_outbox.backoff_for(i) for i in range(1, 12)]
    assert seq[:4] == [60, 300, 900, 3600]
    assert all(x == 3600 for x in seq[4:])
    assert notify_outbox.backoff_for(0) == 60


# ---- message -----------------------------------------------------------------

def test_build_message_headers_and_alternatives(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "h")
    monkeypatch.setenv("SMTP_FROM", "CentralDSP <alerts@example.com>")
    cfg = notify_outbox.smtp_config()
    row = {
        "recipient": "ops@example.com", "kind": "tech_alert", "subject": "Firing: BackendNotReady",
        "body_text": "plain", "body_html": "<b>html</b>",
    }
    msg = notify_outbox.build_message(cfg, row)
    assert msg["To"] == "ops@example.com"
    assert msg["Subject"] == "[CentralDSP] Firing: BackendNotReady"
    assert msg["X-CentralDSP-Kind"] == "tech_alert"
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg.get_content_type() == "multipart/alternative"
    parts = [p.get_content_type() for p in msg.iter_parts()]
    assert parts == ["text/plain", "text/html"]


def test_build_message_plain_only(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "h")
    monkeypatch.setenv("SMTP_FROM", "alerts@example.com")
    row = {"recipient": "x@y", "kind": "k", "subject": "s", "body_text": "t", "body_html": None}
    msg = notify_outbox.build_message(notify_outbox.smtp_config(), row)
    assert msg.get_content_type() == "text/plain"


# ---- dispatch over a fake pool -------------------------------------------------

class _FakeConn:
    """Минимальный asyncpg-подобный коннект над списком строк в памяти."""

    def __init__(self, rows: list[dict], log: list):
        self.rows, self.log = rows, log

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetchrow(self, sql, *args):
        if "FOR UPDATE SKIP LOCKED" in sql:
            due = [r for r in self.rows if r["status"] == "queued" and not r.get("_locked")]
            if not due:
                return None
            due.sort(key=lambda r: r["id"])
            return dict(due[0])
        if "count(*) AS queued" in sql:
            q = [r for r in self.rows if r["status"] == "queued"]
            return {"queued": len(q), "oldest": 100.0 if q else 0.0}
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        self.log.append((sql.split("SET", 1)[1].split("WHERE")[0].strip() if "SET" in sql else sql, args))
        row = next(r for r in self.rows if r["id"] == args[0])
        if "status='sent'" in sql:
            row.update(status="sent", attempts=args[1])
        elif "status='failed'" in sql:
            row.update(status="failed", attempts=args[1], last_error=args[2])
        else:
            row.update(attempts=args[1], last_error=args[2], _locked=True)  # ушла в будущее — в этом проходе не видна
        return "UPDATE 1"


class _FakePool:
    def __init__(self, rows):
        self.rows, self.log = rows, []

    def acquire(self):
        return self

    async def __aenter__(self):
        return _FakeConn(self.rows, self.log)

    async def __aexit__(self, *exc):
        return False

    async def fetchrow(self, sql, *args):
        return await _FakeConn(self.rows, self.log).fetchrow(sql, *args)


def _row(i, **kw):
    base = {"id": i, "recipient": f"u{i}@example.com", "kind": "k", "subject": f"s{i}",
            "body_text": "t", "body_html": None, "status": "queued", "attempts": 0}
    base.update(kw)
    return base


def _gauge(name):
    for line in obs_metrics.render_prometheus().splitlines():
        if line.startswith(name + " "):
            return float(line.split()[1])
    return None


def test_dispatch_noop_without_smtp():
    pool = _FakePool([_row(1), _row(2)])
    res = asyncio.run(notify_outbox.dispatch(pool))
    assert res == {"configured": False, "sent": 0, "failed": 0, "retry": 0, "skipped": 0}
    assert all(r["status"] == "queued" for r in pool.rows)
    assert _gauge("notify_smtp_configured") == 0.0
    assert _gauge("notify_outbox_queued") == 2.0


def test_dispatch_sends_each_row_in_its_own_transaction(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "h")
    monkeypatch.setenv("SMTP_FROM", "alerts@example.com")
    sent = []

    async def fake_send(cfg, msg):
        sent.append(msg["To"])

    pool = _FakePool([_row(1), _row(2), _row(3)])
    res = asyncio.run(notify_outbox.dispatch(pool, sender=fake_send))
    assert res["sent"] == 3 and res["retry"] == 0 and res["failed"] == 0
    assert sent == ["u1@example.com", "u2@example.com", "u3@example.com"]
    assert all(r["status"] == "sent" for r in pool.rows)
    assert _gauge("notify_outbox_queued") == 0.0
    assert obs_metrics.snapshot()["counters"]["notify_email_sent_total"] == 3


def test_dispatch_failure_reschedules_then_gives_up(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "h")
    monkeypatch.setenv("SMTP_FROM", "alerts@example.com")
    monkeypatch.setattr(notify_outbox, "MAX_ATTEMPTS", 3)

    async def boom(cfg, msg):
        raise ConnectionRefusedError("smtp down")

    # Первая неудача — retry с backoff 60 с, остальные строки не откатываются.
    pool = _FakePool([_row(1), _row(2, attempts=2)])
    res = asyncio.run(notify_outbox.dispatch(pool, sender=boom))
    assert res["retry"] == 1 and res["failed"] == 1
    r1, r2 = pool.rows
    assert r1["status"] == "queued" and r1["attempts"] == 1 and "ConnectionRefusedError" in r1["last_error"]
    assert r2["status"] == "failed" and r2["attempts"] == 3
    retry_sql, retry_args = next(e for e in pool.log if "next_attempt_at" in e[0])
    assert retry_args[3] == 60.0
    c = obs_metrics.snapshot()["counters"]
    assert c["notify_email_retry_total"] == 1 and c["notify_email_failed_total"] == 1


def test_dispatch_respects_limit(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "h")
    monkeypatch.setenv("SMTP_FROM", "alerts@example.com")

    async def ok(cfg, msg):
        return None

    pool = _FakePool([_row(i) for i in range(1, 6)])
    res = asyncio.run(notify_outbox.dispatch(pool, limit=2, sender=ok))
    assert res["sent"] == 2
    assert sum(r["status"] == "sent" for r in pool.rows) == 2


# ---- wiring invariants -----------------------------------------------------------

def test_notify_dispatch_job_is_wired_everywhere():
    import scheduler
    from routers import cron

    assert "notify_dispatch" in obs_metrics.CRON_JOBS
    job = next(j for j in scheduler.DEFAULT_JOBS if j["name"] == "notify_dispatch")
    assert job["path"] == "/cron/notify-dispatch" and job["schedule"] == "* * * * *"
    assert job["timeout_seconds"] <= 55  # раз в минуту — не должен налезать на следующий тик
    assert any(r.path == "/cron/notify-dispatch" for r in cron.router.routes)


def test_notification_alert_rules_exist_and_have_runbook():
    import yaml

    doc = yaml.safe_load((ROOT / "ops" / "prometheus" / "alerts.yml").read_text(encoding="utf-8"))
    group = next(g for g in doc["groups"] if g["name"] == "centraldsp-notifications")
    names = {r["alert"] for r in group["rules"]}
    assert names == {"NotifySmtpNotConfigured", "NotifyOutboxStuck", "NotifyEmailsFailing"}
    for r in group["rules"]:
        assert "rtb_alerts.md#notifications" in r["annotations"]["description"]
    tests = yaml.safe_load((ROOT / "ops" / "prometheus" / "alerts_test.yml").read_text(encoding="utf-8"))
    tested = {
        t["expr"].split('alertname="')[1].split('"')[0]
        for case in tests["tests"] for t in case.get("promql_expr_test", []) if 'alertname="' in t["expr"]
    }
    assert names <= tested


def test_env_example_documents_smtp():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("SMTP_HOST", "SMTP_PORT", "SMTP_FROM", "SMTP_STARTTLS", "NOTIFY_MAX_ATTEMPTS"):
        assert key in text, key
