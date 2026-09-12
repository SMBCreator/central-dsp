"""C4b / N-03 (DB): миграция 056 идемпотентна; run_checks на живой схеме —
бюджет (daily/total), остановлена системой, даты, нет показов, низкий баланс;
повторный проход ничего не добавляет; notify_inherit=false глушит письма, но
не in-app. Требует DATABASE_URL (skip иначе)."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg  # noqa: E402

import notify_business as nb  # noqa: E402
import obs_metrics  # noqa: E402

MIG = Path(__file__).resolve().parents[1] / "migrations" / "056_business_notifications.sql"


@pytest.fixture(autouse=True)
def _reset():
    obs_metrics.reset_for_tests()


async def _pool():
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    await pool.execute(MIG.read_text(encoding="utf-8"))
    await pool.execute(MIG.read_text(encoding="utf-8"))
    return pool


def test_checks_end_to_end():
    async def scenario():
        pool = await _pool()
        tag = uuid.uuid4().hex[:10]
        today = date.today()
        uid = await pool.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1, 'x', 'o', 'advertiser') RETURNING id",
            f"owner-{tag}@example.com",
        )
        adv = None
        try:
            adv = await pool.fetchval(
                "INSERT INTO advertisers (user_id, name, balance, notify_emails) VALUES ($1, $2, 50, $3) RETURNING id",
                uid, f"adv-{tag}", [f"adv-{tag}@example.com"],
            )

            async def camp(name, **cols):
                base = {"advertiser_id": adv, "user_id": uid, "name": f"{name}-{tag}", "status": "active",
                        "daily_budget": 0, "total_budget": 0, "start_date": today - timedelta(days=3)}
                base.update(cols)
                keys = list(base)
                return await pool.fetchval(
                    f"INSERT INTO campaigns ({', '.join(keys)}) VALUES ({', '.join(f'${i+1}' for i in range(len(keys)))}) RETURNING id",
                    *base.values(),
                )

            c_daily = await camp("daily", daily_budget=100, spent_today=100, spent_today_date=today)
            c_total = await camp("total", status="paused", total_budget=500, spent_total=500, stop_reason="total_budget",
                                 stopped_at=None, notify_emails=[f"camp-{tag}@example.com"])
            c_soon = await camp("soon", end_date=today + timedelta(days=1))
            c_ended = await camp("ended", end_date=today - timedelta(days=1), notify_inherit=False)
            c_noimps = await camp("noimps")
            await pool.execute("UPDATE campaigns SET updated_at = now() - interval '5 hours', stopped_at = now() - interval '1 hour' WHERE id = ANY($1::uuid[])", [c_noimps, c_total])
            # расход 20/день за 7 дней при балансе 50 -> low_balance при NOTIFY_LOW_BALANCE_DAYS=1? 50 >= 20 -> нет.
            # Делаем 70/день: баланс 50 < 70 -> да. Показы на c_daily, чтобы он не попал в no_impressions.
            for d in range(7):
                await pool.execute(
                    "INSERT INTO stats_hourly (ts_hour, campaign_id, impressions, spend) VALUES (date_trunc('hour', now()) - ($1::int * interval '1 day'), $2, 10, 70)",
                    d, c_daily,
                )
            r1 = await nb.run_checks(pool, today)
            assert r1["budget_exhausted"] >= 2 and r1["campaign_stopped_by_system"] >= 1
            assert r1["dates"] >= 2 and r1["campaign_no_impressions"] >= 1 and r1["low_balance"] >= 1
            kinds = {(row["kind"], row["entity_id"]) for row in await pool.fetch(
                "SELECT kind, entity_id FROM business_alert_log WHERE entity_id = ANY($1::uuid[])",
                [c_daily, c_total, c_soon, c_ended, c_noimps, adv])}
            assert ("budget_exhausted", c_daily) in kinds and ("budget_exhausted", c_total) in kinds
            assert ("campaign_stopped_by_system", c_total) in kinds
            assert ("campaign_ending_soon", c_soon) in kinds and ("campaign_ended", c_ended) in kinds
            assert ("campaign_no_impressions", c_noimps) in kinds and ("campaign_no_impressions", c_daily) not in kinds
            assert ("low_balance", adv) in kinds

            ob = await pool.fetch("SELECT recipient, kind, entity_id, source FROM notification_outbox WHERE recipient LIKE $1", f"%{tag}%")
            by_kind = {}
            for o in ob:
                by_kind.setdefault((o["kind"], uuid.UUID(o["entity_id"])), []).append((o["recipient"], o["source"]))
            # campaign.notify_emails побеждает advertiser.notify_emails.
            assert by_kind[("budget_exhausted", c_total)] == [(f"camp-{tag}@example.com", "business:campaign")]
            assert by_kind[("budget_exhausted", c_daily)] == [(f"adv-{tag}@example.com", "business:advertiser")]
            # notify_inherit=false: письма нет, in-app есть.
            assert ("campaign_ended", c_ended) not in by_kind
            n_ended = await pool.fetchval("SELECT count(*) FROM notifications WHERE user_id = $1 AND type = 'campaign_ended'", uid)
            assert n_ended == 1
            assert by_kind[("low_balance", adv)] == [(f"adv-{tag}@example.com", "business:advertiser")]

            # Повторный проход — идемпотентен.
            before = await pool.fetchval("SELECT count(*) FROM notifications WHERE user_id = $1", uid)
            r2 = await nb.run_checks(pool, today)
            assert all(v == 0 for v in r2.values()), r2
            assert await pool.fetchval("SELECT count(*) FROM notifications WHERE user_id = $1", uid) == before
            assert obs_metrics.snapshot()["counters"]["notify_business_deduplicated_total"] >= 6
        finally:
            await pool.execute("DELETE FROM notification_outbox WHERE recipient LIKE $1", f"%{tag}%")
            if adv is not None:
                ids = [r["id"] for r in await pool.fetch("SELECT id FROM campaigns WHERE advertiser_id = $1", adv)] + [adv]
                await pool.execute("DELETE FROM business_alert_log WHERE entity_id = ANY($1::uuid[])", ids)
                await pool.execute("DELETE FROM stats_hourly WHERE campaign_id = ANY($1::uuid[])", ids)
                await pool.execute("DELETE FROM campaigns WHERE advertiser_id = $1", adv)
                await pool.execute("DELETE FROM advertisers WHERE id = $1", adv)
            await pool.execute("DELETE FROM notifications WHERE user_id = $1", uid)
            await pool.execute("DELETE FROM users WHERE id = $1", uid)
            await pool.close()

    asyncio.run(scenario())
