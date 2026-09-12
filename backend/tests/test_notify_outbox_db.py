"""C4b / N-01 (DB): миграция 054 идемпотентна; enqueue дедуплицирует по
(recipient, dedup_key) и нормализует адрес; dispatch претендует строки через
SKIP LOCKED (параллельный dispatcher не отправит письмо дважды); неудача →
backoff в next_attempt_at и строка невидима до срока; purge_old не трогает queued.

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

import notify_outbox  # noqa: E402
import obs_metrics  # noqa: E402

MIG = Path(__file__).resolve().parents[1] / "migrations" / "054_notification_outbox.sql"


@pytest.fixture(autouse=True)
def _smtp(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_FROM", "alerts@test")
    obs_metrics.reset_for_tests()


def _run(coro):
    return asyncio.run(coro)


async def _pool():
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
    await pool.execute(MIG.read_text(encoding="utf-8"))
    await pool.execute(MIG.read_text(encoding="utf-8"))  # идемпотентна
    return pool


def test_enqueue_dedup_and_normalisation():
    async def scenario():
        pool = await _pool()
        tag = uuid.uuid4().hex
        try:
            n = await notify_outbox.enqueue_email(
                pool, recipients=[f" A-{tag}@Example.com ", f"a-{tag}@example.com", "garbage", ""],
                subject="s", body_text="t", kind="k", dedup_key=f"d-{tag}",
            )
            assert n == 1  # регистр/пробелы схлопнуты, мусор отброшен
            n2 = await notify_outbox.enqueue_email(
                pool, recipients=[f"a-{tag}@example.com"], subject="s", body_text="t", kind="k", dedup_key=f"d-{tag}",
            )
            assert n2 == 0  # тот же dedup_key — молча пропущено
            n3 = await notify_outbox.enqueue_email(
                pool, recipients=[f"a-{tag}@example.com"], subject="s", body_text="t", kind="k",
            )
            assert n3 == 1  # без dedup_key — новая строка
            rows = await pool.fetch(
                "SELECT recipient, status FROM notification_outbox WHERE recipient = $1", f"a-{tag}@example.com"
            )
            assert len(rows) == 2 and all(r["status"] == "queued" for r in rows)
        finally:
            await pool.execute("DELETE FROM notification_outbox WHERE recipient LIKE $1", f"%{tag}%")
            await pool.close()

    _run(scenario())


def test_dispatch_skip_locked_and_backoff_visibility():
    async def scenario():
        pool = await _pool()
        tag = uuid.uuid4().hex
        rcpt = f"u-{tag}@example.com"
        try:
            await notify_outbox.enqueue_email(pool, recipients=[rcpt], subject="s", body_text="t", kind="k")
            row_id = await pool.fetchval("SELECT id FROM notification_outbox WHERE recipient=$1", rcpt)

            # Конкурент держит строку под FOR UPDATE — наш dispatch её не видит.
            async with pool.acquire() as other:
                async with other.transaction():
                    await other.fetchrow(
                        "SELECT id FROM notification_outbox WHERE id=$1 FOR UPDATE", row_id
                    )
                    sent = []

                    async def ok(cfg, msg):
                        sent.append(msg["To"])

                    res = await notify_outbox.dispatch(pool, sender=ok)
                    assert res["sent"] == 0 and sent == []

            # Строка свободна: неудача → attempts=1, next_attempt_at в будущем.
            async def boom(cfg, msg):
                raise TimeoutError("smtp timeout")

            res = await notify_outbox.dispatch(pool, sender=boom)
            assert res["retry"] == 1
            r = await pool.fetchrow(
                "SELECT status, attempts, last_error, next_attempt_at > now() + interval '50 seconds' AS deferred "
                "FROM notification_outbox WHERE id=$1", row_id,
            )
            assert r["status"] == "queued" and r["attempts"] == 1 and r["deferred"] is True
            assert "TimeoutError" in r["last_error"]

            # Пока срок не пришёл — dispatcher её не берёт даже с рабочим SMTP.
            res = await notify_outbox.dispatch(pool, sender=ok)
            assert res["sent"] == 0

            # Срок пришёл — отправлена, last_error очищен.
            await pool.execute("UPDATE notification_outbox SET next_attempt_at = now() WHERE id=$1", row_id)
            res = await notify_outbox.dispatch(pool, sender=ok)
            assert res["sent"] == 1 and sent == [rcpt]
            r = await pool.fetchrow("SELECT status, attempts, last_error, sent_at FROM notification_outbox WHERE id=$1", row_id)
            assert r["status"] == "sent" and r["attempts"] == 2 and r["last_error"] is None and r["sent_at"] is not None
        finally:
            await pool.execute("DELETE FROM notification_outbox WHERE recipient = $1", rcpt)
            await pool.close()

    _run(scenario())


def test_purge_old_keeps_queued():
    async def scenario():
        pool = await _pool()
        tag = uuid.uuid4().hex
        try:
            for status in ("sent", "failed", "queued"):
                await pool.execute(
                    """INSERT INTO notification_outbox (channel, recipient, kind, subject, body_text, status, created_at)
                       VALUES ('email', $1, 'k', 's', 't', $2, now() - interval '40 days')""",
                    f"{status}-{tag}@example.com", status,
                )
            deleted = await notify_outbox.purge_old(pool, days=30)
            assert deleted >= 2
            left = await pool.fetch("SELECT status FROM notification_outbox WHERE recipient LIKE $1", f"%{tag}%")
            assert [r["status"] for r in left] == ["queued"]
        finally:
            await pool.execute("DELETE FROM notification_outbox WHERE recipient LIKE $1", f"%{tag}%")
            await pool.close()

    _run(scenario())
