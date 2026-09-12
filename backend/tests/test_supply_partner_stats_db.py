"""GET /supply-partners/{id}/stats — карточка SSP на реальном Postgres (нужен DATABASE_URL).

Фиксируем: метрики считаются из bid_events только по своему партнёру и без is_test,
win_rate/ctr в процентах, spend = sum(clear_price по win)/1000 (stats_math.spend_expr, как в /stats/report),
loss_reasons с русскими подписями и долями, чужой партнёр -> 404.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg  # noqa: E402
import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

import db  # noqa: E402
from auth import current_user  # noqa: E402
from routers import supply_partners as sp_router  # noqa: E402
from tests.test_idor_isolation_db import _Tenant  # noqa: E402


@pytest.fixture
def env():
    db._pool = None
    yield
    db._pool = None


def _client(user_row: dict) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(sp_router.router)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _event(conn, partner, camp, li, cr, event_type: str, **kw):
    await conn.execute(
        """INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id, supply_partner_id,
                                   price, clear_price, bidfloor, loss_reason, min_to_win, tagid, publisher_id,
                                   domain, ad_type, is_test, created_at)
           VALUES ($1, gen_random_uuid()::text, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, now())""",
        event_type, camp, li, cr, partner,
        kw.get("price", 100.0), kw.get("clear_price"), kw.get("bidfloor"), kw.get("loss_reason"),
        kw.get("min_to_win"), kw.get("tagid", "tag-1"), kw.get("publisher_id", "pub-1"),
        kw.get("domain", "site.example"), kw.get("ad_type", "banner"), kw.get("is_test", False),
    )


def test_partner_stats_totals_losses_and_isolation(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        alice = await _Tenant("sspA").setup(conn)
        bob = await _Tenant("sspB").setup(conn)
        partner = await conn.fetchval(
            """INSERT INTO supply_partners (user_id, name, endpoint_key, auction_type, bid_floor, qps_limit,
                                            allowed_ad_types, status)
               VALUES ($1, 'SSP A', 'ek-' || gen_random_uuid()::text, 1, 0, 100, '["banner"]'::jsonb, 'active')
               RETURNING id""",
            alice.ids["user"],
        )
        camp, li, cr = alice.ids["camp"], alice.ids["li"], alice.ids["cr"]
        try:
            # 4 ставки, 2 выигрыша, 2 показа (clear 1500 + 500 CPM), 1 клик, 2 лосса разных причин
            for _ in range(4):
                await _event(conn, partner, camp, li, cr, "bid", price=120.0, bidfloor=50.0)
            # Договор о расходе (stats_math): clear_price несёт win, показы — нет.
            await _event(conn, partner, camp, li, cr, "win", clear_price=1500.0)
            await _event(conn, partner, camp, li, cr, "win", clear_price=500.0)
            for _ in range(2):
                await _event(conn, partner, camp, li, cr, "impression")
            await _event(conn, partner, camp, li, cr, "click")
            await _event(conn, partner, camp, li, cr, "loss", loss_reason="102", min_to_win=140.0)
            await _event(conn, partner, camp, li, cr, "loss", loss_reason="100")
            # шум: тестовое событие и событие без партнёра — не должны попасть в карточку
            await _event(conn, partner, camp, li, cr, "bid", is_test=True)
            await _event(conn, None, camp, li, cr, "bid")

            r = await _client(alice.as_user()).get(f"/supply-partners/{partner}/stats", params={"days": 7})
            assert r.status_code == 200, r.text
            body = r.json()
            t = body["totals"]
            assert (t["bids"], t["wins"], t["impressions"], t["clicks"], t["losses"]) == (4, 2, 2, 1, 2)
            assert t["win_rate"] == 50.0 and t["ctr"] == 50.0
            assert t["spend"] == 2.0  # (1500 + 500) / 1000
            assert t["avg_bid"] == 120.0 and t["avg_bidfloor"] == 50.0 and t["avg_min_to_win"] == 140.0

            reasons = {x["reason"]: x for x in body["loss_reasons"]}
            assert reasons["102"]["label"] == "Проиграли по цене" and reasons["102"]["share"] == 50.0
            assert reasons["100"]["label"] == "Ставка ниже bidfloor"

            assert len(body["daily"]) == 1 and body["daily"][0]["bids"] == 4
            assert body["placements"][0]["tagid"] == "tag-1" and body["placements"][0]["bids"] == 4
            assert body["ad_types"][0]["ad_type"] == "banner"

            # Валидация окна и изоляция владельца
            r = await _client(alice.as_user()).get(f"/supply-partners/{partner}/stats", params={"days": 0})
            assert r.status_code == 422
            r = await _client(bob.as_user()).get(f"/supply-partners/{partner}/stats")
            assert r.status_code == 404
        finally:
            await conn.execute("DELETE FROM bid_events WHERE campaign_id = $1", camp)
            await conn.execute("DELETE FROM supply_partners WHERE id = $1", partner)
            await alice.teardown(conn)
            await bob.teardown(conn)
            await conn.close()

    asyncio.run(scenario())
