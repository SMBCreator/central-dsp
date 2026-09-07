"""Audit E-04: OpenRTB test=1 сквозь всю воронку.

Тестовый бид (SSP-проверка интеграции или встроенный симулятор) должен:
- попадать в журнал bid_events с is_test=true на КАЖДОМ шаге
  (win / impression / viewable / video / click) — видно в отчёте с флагом;
- не списывать деньги (transactions) и не трогать stats_hourly /
  domain_stats_hourly — иначе демо-прогон искажает расход, CTR и win-rate;
- конверсия пикселя по такому clid наследует is_test и не идёт в hourly/CPA.

Боевой бид того же формата рядом — контроль, что фильтр не «глушит» всё.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

from routers import pixel as pixel_module
from routers import rtb as rtb_module

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


class _FailingRedis:
    async def exists(self, *_):
        raise ConnectionError("redis down")

    async def set(self, *_, **__):
        raise ConnectionError("redis down")

    async def get(self, *_):
        return None

    async def incr(self, *_):
        raise ConnectionError("redis down")


def _row(camp, li, cr, sp, *, is_test: bool):
    return {
        "imp_id": "1", "campaign_id": camp, "line_item_id": li, "creative_id": cr,
        "supply_partner_id": sp, "device_type": "desktop", "geo_country": "RU",
        "ad_type": "video", "domain": "site.ru", "os": "linux", "browser": "chrome",
        "bundle": None, "publisher_id": None, "tagid": None, "geo_region": None, "geo_city": None,
        "price": 100.0, "is_test": is_test,
        "advertiser_id": "", "reserve": 0, "has_total": 0, "has_li": 0, "business_date": "2026-09-07",
    }


async def _stats(conn, camp) -> dict:
    row = await conn.fetchrow(
        """SELECT coalesce(sum(impressions),0) imps, coalesce(sum(clicks),0) clicks,
                  coalesce(sum(viewable_imps),0) views, coalesce(sum(video_starts),0) vstarts,
                  coalesce(sum(video_completes),0) vcompl, coalesce(sum(conversions),0) conv,
                  coalesce(sum(spend),0) spend
           FROM stats_hourly WHERE campaign_id = $1""",
        camp,
    )
    return {k: (float(v) if k == "spend" else int(v)) for k, v in dict(row).items()}


async def _journal(conn, bid) -> dict[str, bool]:
    rows = await conn.fetch("SELECT event_type, is_test FROM bid_events WHERE bid_id = $1", bid)
    return {r["event_type"]: r["is_test"] for r in rows}


def test_test_bid_funnel_is_journal_only(monkeypatch):
    async def main():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
        camp, li, cr, sp = (uuid.uuid4() for _ in range(4))
        test_bid = f"t-{uuid.uuid4().hex}"
        rows = {test_bid: _row(camp, li, cr, sp, is_test=True)}

        async def fake_find_bid(_pool, bid):
            return rows.get(bid)

        async def fake_get_pool():
            return pool

        monkeypatch.setattr(rtb_module, "_find_bid", fake_find_bid)
        monkeypatch.setattr(rtb_module, "get_pool", fake_get_pool)
        monkeypatch.setattr(rtb_module, "get_redis", lambda: _FailingRedis())
        monkeypatch.setattr(rtb_module, "verify_event_token", lambda t: t)
        monkeypatch.setattr(rtb_module, "_fire_and_forget", lambda coro: coro.close())

        pixel_id = goal_id = owner = None
        try:
            # --- Воронка тестового бида ---------------------------------------
            r = await rtb_module.win_notice(bid=test_bid, price=80.0)
            assert r.get("test") is True
            await rtb_module.impression(test_bid)
            await rtb_module.viewable_impression(test_bid)
            await rtb_module.video_event(test_bid, "start")
            await rtb_module.video_event(test_bid, "complete")
            await rtb_module.click(test_bid)

            async with pool.acquire() as conn:
                # Журнал: каждое событие есть и помечено is_test.
                j = await _journal(conn, test_bid)
                assert set(j) >= {"win", "impression", "viewable", "video_start", "video_complete", "click"}, j
                assert all(j.values()), f"все события тестового бида должны быть is_test: {j}"

                # Деньги и статистика клиента — нетронуты.
                assert _stats_zero(await _stats(conn, camp)), await _stats(conn, camp)
                assert await conn.fetchval(
                    "SELECT count(*) FROM transactions WHERE bid_id = $1", test_bid
                ) == 0, "тестовый win не должен списывать деньги"
                assert await conn.fetchval(
                    "SELECT count(*) FROM domain_stats_hourly WHERE campaign_id = $1", camp
                ) == 0

                # Отчёт клиента (NOT is_test) этого бида не видит; аудиторский — видит.
                assert await conn.fetchval(
                    "SELECT count(*) FROM bid_events WHERE campaign_id = $1 AND NOT is_test", camp
                ) == 0

                # --- Конверсия пикселя по тестовому clid -----------------------
                # bid-событие нужно, чтобы _match_goals нашёл атрибуцию по clid.
                await conn.execute(
                    """INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id,
                                               supply_partner_id, domain, device_type, geo_country, is_test)
                       VALUES ('bid', $1, $2, $3, $4, $5, 'site.ru', 'desktop', 'RU', true)""",
                    test_bid, camp, li, cr, sp,
                )
                # CI-база пустая — владельца пикселя создаём сами.
                owner = await conn.fetchval(
                    """INSERT INTO users (email, password_hash, name, role)
                       VALUES ($1, 'x', 'e04', 'advertiser') RETURNING id""",
                    f"e04-{uuid.uuid4().hex[:8]}@test.local",
                )
                pixel_id = await conn.fetchval(
                    "INSERT INTO pixels (user_id, name, domain) VALUES ($1, 'e04', 'site.ru') RETURNING id", owner
                )
                goal_id = await conn.fetchval(
                    """INSERT INTO goals (pixel_id, name, goal_type, match_value, value)
                       VALUES ($1, 'lead', 'event', 'lead', 10) RETURNING id""",
                    pixel_id,
                )

            await pixel_module._match_goals(
                pool, str(pixel_id), "sess-e04", "event", "lead", "https://site.ru/thanks", test_bid,
            )

            async with pool.acquire() as conn:
                conv = await conn.fetchrow(
                    "SELECT is_test, campaign_id FROM conversions WHERE goal_id = $1", goal_id
                )
                assert conv is not None and conv["is_test"] is True, dict(conv or {})
                assert conv["campaign_id"] == camp, "атрибуция сохраняется — это нужно для аудита"
                assert (await _stats(conn, camp))["conv"] == 0, "тест-конверсия не идёт в hourly/CPA"
                # Клиентские витрины пикселя её не считают.
                assert await conn.fetchval(
                    "SELECT count(*) FROM conversions WHERE goal_id = $1 AND NOT is_test", goal_id
                ) == 0
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM bid_event_once WHERE bid_id = $1", test_bid)
                await conn.execute("DELETE FROM bid_events WHERE campaign_id = $1", camp)
                await conn.execute("DELETE FROM stats_hourly WHERE campaign_id = $1", camp)
                await conn.execute("DELETE FROM domain_stats_hourly WHERE campaign_id = $1", camp)
                if goal_id:
                    await conn.execute("DELETE FROM conversions WHERE goal_id = $1", goal_id)
                    await conn.execute("DELETE FROM goals WHERE id = $1", goal_id)
                if pixel_id:
                    await conn.execute("DELETE FROM pixel_events WHERE pixel_id = $1", pixel_id)
                    await conn.execute("DELETE FROM pixels WHERE id = $1", pixel_id)
                if owner:
                    await conn.execute("DELETE FROM users WHERE id = $1", owner)
            await pool.close()

    asyncio.run(main())


def _stats_zero(s: dict) -> bool:
    return all(v == 0 for v in s.values())
