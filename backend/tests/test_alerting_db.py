"""C4b / N-02 (DB): миграция 055 идемпотентна; webhook -> alert_events (дедуп по
fingerprint/status/starts_at), in-app суперадминам, письма в notification_outbox;
настройки получателей через platform_settings; /alerts/events отдаёт активные.

Требует DATABASE_URL (skip иначе).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg  # noqa: E402

import alerting  # noqa: E402
import obs_metrics  # noqa: E402

MIGS = [Path(__file__).resolve().parents[1] / "migrations" / f for f in ("054_notification_outbox.sql", "055_alert_events.sql")]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    obs_metrics.reset_for_tests()


async def _pool():
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    for m in MIGS:
        await pool.execute(m.read_text(encoding="utf-8"))
        await pool.execute(m.read_text(encoding="utf-8"))
    return pool


def _payload(fp, status="firing", severity="critical", starts="2026-09-12T10:00:00Z"):
    return {"externalURL": "http://am", "alerts": [{
        "status": status,
        "labels": {"alertname": "TestAlert", "severity": severity, "instance": "backend:8000"},
        "annotations": {"summary": "sum", "description": "desc"},
        "startsAt": starts, "endsAt": "0001-01-01T00:00:00Z" if status == "firing" else "2026-09-12T11:00:00Z",
        "fingerprint": fp,
    }]}


def test_webhook_flow_end_to_end():
    async def scenario():
        pool = await _pool()
        tag = uuid.uuid4().hex[:10]
        fp = f"fp-{tag}"
        email = f"root-{tag}@example.com"
        uid = await pool.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1, 'x', 'root', 'superadmin') RETURNING id", email
        )
        try:
            # Настройки пусты -> письмо на email суперадмина.
            recipients, source = await alerting.resolve_recipients(pool)
            assert email in recipients and source == "superadmins"

            r1 = await alerting.process_payload(pool, _payload(fp))
            assert r1["stored"] == 1 and r1["in_app"] >= 1 and r1["emails_queued"] >= 1
            r2 = await alerting.process_payload(pool, _payload(fp))
            assert r2["duplicates"] == 1 and r2["stored"] == 0
            r3 = await alerting.process_payload(pool, _payload(fp, status="resolved"))
            assert r3["stored"] == 1

            rows = await pool.fetch("SELECT status, severity, labels->>'instance' AS inst FROM alert_events WHERE fingerprint=$1 ORDER BY id", fp)
            assert [r["status"] for r in rows] == ["firing", "resolved"] and rows[0]["inst"] == "backend:8000"
            n = await pool.fetch("SELECT type, title, link FROM notifications WHERE user_id=$1 ORDER BY created_at", uid)
            assert len(n) == 2 and n[0]["type"] == "tech_alert" and n[0]["link"] == alerting.IN_APP_LINK
            ob = await pool.fetch("SELECT kind, status, dedup_key FROM notification_outbox WHERE recipient=$1 ORDER BY id", email)
            assert [o["kind"] for o in ob] == ["tech_alert", "tech_alert"] and all(o["status"] == "queued" for o in ob)
            assert ob[0]["dedup_key"] != ob[1]["dedup_key"]

            # Настройки в platform_settings перекрывают fallback.
            await alerting.save_settings(pool, alerting.normalise_settings({"emails": [f"ops-{tag}@example.com"]}), uid)
            recipients, source = await alerting.resolve_recipients(pool)
            assert recipients == [f"ops-{tag}@example.com"] and source == "settings"
        finally:
            await pool.execute("DELETE FROM platform_settings WHERE key = $1", alerting.SETTINGS_KEY)
            await pool.execute("DELETE FROM notification_outbox WHERE recipient LIKE $1", f"%{tag}%")
            await pool.execute("DELETE FROM alert_events WHERE fingerprint = $1", fp)
            await pool.execute("DELETE FROM users WHERE id = $1", uid)
            await pool.close()

    asyncio.run(scenario())


def test_outbox_route_lists_recent_mail_with_status_filter_and_counts(monkeypatch):
    """GET /alerts/outbox (N-04): свежие письма первыми, фильтр по статусу, счётчики по
    статусам и никакого тела письма в ответе (там данные чужих кампаний)."""
    import notify_outbox
    from routers import alerts as alerts_router

    async def scenario():
        pool = await _pool()
        tag = uuid.uuid4().hex[:10]
        rcpt = f"ob-{tag}@example.com"

        async def fake_get_pool():
            return pool

        monkeypatch.setattr(alerts_router, "get_pool", fake_get_pool)
        try:
            for i in range(3):
                await notify_outbox.enqueue_email(
                    pool, recipients=[rcpt], kind="tech_alert_test", source="test",
                    subject=f"subj-{tag}-{i}", body_text="secret body", body_html="<p>secret body</p>",
                )
            failed_id = await pool.fetchval(
                "UPDATE notification_outbox SET status='failed', attempts=8, last_error='boom' "
                "WHERE recipient=$1 AND subject=$2 RETURNING id", rcpt, f"subj-{tag}-0",
            )

            res = await alerts_router.list_outbox(limit=200, status=None, user={"role": "superadmin"})
            mine = [r for r in res["items"] if r["recipient"] == rcpt]
            assert [r["subject"] for r in mine] == [f"subj-{tag}-2", f"subj-{tag}-1", f"subj-{tag}-0"]
            assert all("body_text" not in r and "body_html" not in r for r in res["items"])
            assert res["counts"].get("failed", 0) >= 1 and res["counts"].get("queued", 0) >= 2

            only_failed = await alerts_router.list_outbox(limit=200, status="failed", user={"role": "superadmin"})
            assert all(r["status"] == "failed" for r in only_failed["items"])
            row = next(r for r in only_failed["items"] if r["id"] == failed_id)
            assert row["attempts"] == 8 and row["last_error"] == "boom"
        finally:
            await pool.execute("DELETE FROM notification_outbox WHERE recipient = $1", rcpt)
            await pool.close()

    asyncio.run(scenario())
