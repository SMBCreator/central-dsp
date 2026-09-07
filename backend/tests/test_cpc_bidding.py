"""Audit R-03: CPC bid type — цена клика, а не CPM.

До этой правки `bid_type = cpc` в UI и БД был, но оба биддера трактовали
`bid_price` как CPM и списывали деньги по win. Договор теперь:

* в аукцион уходит ожидаемая стоимость показа: bid_cpc × CTR × 1000
  (CTR — pCTR-модель, при её отсутствии CPC_FALLBACK_CTR);
* win CPC-бида — событие доставки: журнал + wins, spend = 0, без clear_price;
* оплаченное событие — первый click: bill_win(cost = bid_cpc), clear_price
  клика = цена клика, повторные клики не списывают (ledger по bid_id).

Числа в тестах — те же, что в bidder-go/internal/rtb/cpc_test.go.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import pytest

from ml import pctr as ml_pctr
from stats_math import SPEND_FROM_EVENTS_SQL


# ------------------------------------------------------------- pure math ---

def test_cpc_to_cpm_formula():
    assert ml_pctr.cpc_to_cpm(50.0, 0.002) == pytest.approx(100.0)
    assert ml_pctr.cpc_to_cpm(50.0, 0.01) == pytest.approx(500.0)
    assert ml_pctr.cpc_to_cpm(0.0, 0.5) == 0.0


def test_expected_ctr_falls_back_without_model():
    ml_pctr.reset_cache_for_tests()

    class _NoModelPool:
        async def fetchrow(self, *_):
            return None

    ctr = asyncio.run(ml_pctr.expected_ctr(
        _NoModelPool(), domain="site.ru", creative_id="c1", device_type="desktop", hour=12,
    ))
    assert ctr == ml_pctr.CPC_FALLBACK_CTR == 0.002
    ml_pctr.reset_cache_for_tests()


def test_expected_ctr_uses_model_global_when_no_segments_match():
    ml_pctr.set_cache_for_tests(version=7, global_ctr=0.004, segments={})
    try:
        ctr = asyncio.run(ml_pctr.expected_ctr(
            None, domain="unknown.ru", creative_id="c-none", device_type="tv", hour=3,
        ))
        assert ctr == pytest.approx(0.004)
    finally:
        ml_pctr.reset_cache_for_tests()


def test_expected_ctr_uses_segment_when_matched():
    # Один совпавший сегмент: logit(g) + (logit(seg) - logit(g)) = logit(seg) -> seg.
    ml_pctr.set_cache_for_tests(version=7, global_ctr=0.004, segments={("domain", "site.ru"): 0.02})
    try:
        ctr = asyncio.run(ml_pctr.expected_ctr(
            None, domain="Site.RU", creative_id=None, device_type=None, hour=None,
        ))
        assert ctr == pytest.approx(0.02)
    finally:
        ml_pctr.reset_cache_for_tests()


def test_spend_contract_counts_cpm_wins_and_cpc_clicks():
    sql = SPEND_FROM_EVENTS_SQL.format(e="e")
    assert "event_type = 'win'" in sql and "/ 1000.0" in sql
    assert "event_type = 'click'" in sql


# ------------------------------------------------------------ db funnel ----

DATABASE_URL = os.environ.get("DATABASE_URL")


def _cpc_row(camp, li, cr, sp, *, bid_cpc: float):
    return {
        "imp_id": "1", "campaign_id": camp, "line_item_id": li, "creative_id": cr,
        "supply_partner_id": sp, "device_type": "desktop", "geo_country": "RU",
        "ad_type": "banner", "domain": "site.ru", "os": "linux", "browser": "chrome",
        "bundle": None, "publisher_id": None, "tagid": None, "geo_region": None, "geo_city": None,
        "price": 100.0, "is_test": False,
        "bid_type": "cpc", "bid_cpc": bid_cpc,
        "advertiser_id": "", "reserve": 0, "has_total": 0, "has_li": 0, "business_date": "2026-09-07",
    }


class _NullRedis:
    async def exists(self, *_):
        return 0

    async def set(self, *_, **__):
        return True

    async def get(self, *_):
        return None

    async def incr(self, *_):
        return 1

    async def eval(self, *_, **__):
        return 1

    async def evalsha(self, *_, **__):
        return 1

    async def delete(self, *_):
        return 1


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_cpc_bid_is_billed_on_first_click_only(monkeypatch):
    import asyncpg
    from routers import rtb as rtb_module

    async def main():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
        camp, li, cr, sp = (uuid.uuid4() for _ in range(4))
        bid = f"cpc-{uuid.uuid4().hex}"
        rows = {bid: _cpc_row(camp, li, cr, sp, bid_cpc=12.5)}

        async def fake_find_bid(_pool, b):
            return rows.get(b)

        async def fake_get_pool():
            return pool

        monkeypatch.setattr(rtb_module, "_find_bid", fake_find_bid)
        monkeypatch.setattr(rtb_module, "get_pool", fake_get_pool)
        monkeypatch.setattr(rtb_module, "get_redis", lambda: _NullRedis())
        monkeypatch.setattr(rtb_module, "verify_event_token", lambda t: t)
        monkeypatch.setattr(rtb_module, "_fire_and_forget", lambda coro: coro.close())

        owner = None
        try:
            async with pool.acquire() as conn:
                owner = await conn.fetchval(
                    """INSERT INTO users (email, password_hash, name, role)
                       VALUES ($1, 'x', 'r03', 'advertiser') RETURNING id""",
                    f"r03-{uuid.uuid4().hex[:8]}@test.local",
                )
                adv = await conn.fetchval(
                    "INSERT INTO advertisers (user_id, name, balance) VALUES ($1, 'r03', 1000) RETURNING id",
                    owner,
                )
                await conn.execute(
                    "INSERT INTO campaigns (id, advertiser_id, user_id, name, status) VALUES ($1, $2, $3, 'r03', 'active')",
                    camp, adv, owner,
                )
                await conn.execute(
                    "INSERT INTO line_items (id, campaign_id, user_id, name, bid_type, bid_price) VALUES ($1, $2, $3, 'li', 'cpc', 12.5)",
                    li, camp, owner,
                )

            # --- win: журнал + wins, денег нет ----------------------------------
            r = await rtb_module.win_notice(bid=bid, price=80.0)
            assert r.get("cpc") is True, r
            async with pool.acquire() as conn:
                win = await conn.fetchrow(
                    "SELECT clear_price FROM bid_events WHERE bid_id = $1 AND event_type = 'win'", bid
                )
                assert win is not None and win["clear_price"] is None, dict(win)
                assert await conn.fetchval("SELECT count(*) FROM transactions WHERE bid_id = $1", bid) == 0
                st = await conn.fetchrow(
                    "SELECT coalesce(sum(wins),0) wins, coalesce(sum(spend),0) spend FROM stats_hourly WHERE campaign_id = $1",
                    camp,
                )
                assert int(st["wins"]) == 1 and Decimal(st["spend"]) == 0, dict(st)

            # --- первый клик: списание ровно bid_cpc -----------------------------
            await rtb_module.click(bid)
            async with pool.acquire() as conn:
                tx = await conn.fetch(
                    "SELECT amount, description FROM transactions WHERE bid_id = $1 AND type = 'spend'", bid
                )
                assert len(tx) == 1 and Decimal(tx[0]["amount"]) == Decimal("-12.5"), [dict(t) for t in tx]
                assert tx[0]["description"].startswith("Click bid=")
                click = await conn.fetchrow(
                    "SELECT clear_price FROM bid_events WHERE bid_id = $1 AND event_type = 'click'", bid
                )
                assert Decimal(click["clear_price"]) == Decimal("12.5"), dict(click)
                assert Decimal(await conn.fetchval("SELECT balance FROM advertisers WHERE id = $1", adv)) == Decimal("987.5")
                st = await conn.fetchrow(
                    "SELECT coalesce(sum(clicks),0) clicks, coalesce(sum(spend),0) spend FROM stats_hourly WHERE campaign_id = $1",
                    camp,
                )
                assert int(st["clicks"]) == 1 and Decimal(st["spend"]) == Decimal("12.5"), dict(st)
                # Raw-отчёт по договору stats_math даёт тот же расход, что hourly.
                raw = await conn.fetchval(
                    f"SELECT {SPEND_FROM_EVENTS_SQL.format(e='e')} FROM bid_events e WHERE e.campaign_id = $1 AND NOT e.is_test",
                    camp,
                )
                assert Decimal(raw) == Decimal("12.5"), raw

            # --- повторный клик: журнал дедуплицирован, денег больше нет ----------
            await rtb_module.click(bid)
            async with pool.acquire() as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM transactions WHERE bid_id = $1 AND type = 'spend'", bid
                ) == 1
                assert Decimal(await conn.fetchval("SELECT balance FROM advertisers WHERE id = $1", adv)) == Decimal("987.5")
        finally:
            async with pool.acquire() as conn:
                await conn.execute("ALTER TABLE transactions DISABLE TRIGGER trg_transactions_append_only")
                try:
                    await conn.execute("DELETE FROM transactions WHERE bid_id = $1", bid)
                finally:
                    await conn.execute("ALTER TABLE transactions ENABLE TRIGGER trg_transactions_append_only")
                await conn.execute("DELETE FROM bid_event_once WHERE bid_id = $1", bid)
                await conn.execute("DELETE FROM bid_events WHERE campaign_id = $1", camp)
                await conn.execute("DELETE FROM stats_hourly WHERE campaign_id = $1", camp)
                await conn.execute("DELETE FROM domain_stats_hourly WHERE campaign_id = $1", camp)
                if owner:
                    await conn.execute("DELETE FROM users WHERE id = $1", owner)
            await pool.close()

    asyncio.run(main())
