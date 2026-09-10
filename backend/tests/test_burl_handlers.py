"""R-07 на уровне хендлеров: win_notice / billing_notice / sweep_pending_burl
с фейковым пулом (журнал SQL) и fakeredis (настоящие Lua).

Проверяем ровно то, что обещано пользователю: в режиме burl nurl не
списывает деньги и не поднимает billable_imps; burl списывает один раз;
expire — денег нет, событие win_expired; поздний burl — списание один раз с
флагом late; режим win не изменился.
"""
import asyncio
import contextlib
import time
from decimal import Decimal

import fakeredis.aioredis
import pytest

import cache
import obs_metrics
from billing_core import BillResult
from cache import (
    PENDING_BIDS_KEY,
    PENDING_BURL_KEY,
    avail_adv_key,
    bid_state_key,
    gate_and_reserve,
)
from routers import rtb as rtb_module


class _FakeConn:
    def __init__(self, pool):
        self.pool = pool

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, *args):
        self.pool.executed.append((" ".join(sql.split()), args))

    async def fetchval(self, sql, *args):
        flat = " ".join(sql.split())
        if "INSERT INTO bid_event_once" in flat:
            key = (args[0], args[1])
            if key in self.pool.once:
                return None
            self.pool.once.add(key)
            return True
        if "SELECT 1 FROM bid_event_once" in flat:
            kind = flat.split("kind = '")[1].split("'")[0]
            return 1 if (args[0], kind) in self.pool.once else None
        if "SELECT billing_event" in flat:
            return self.pool.mode
        raise AssertionError(f"unexpected fetchval: {flat}")


class _FakePool:
    def __init__(self, mode: str):
        self.mode = mode
        self.once: set[tuple[str, str]] = set()
        self.executed: list[tuple[str, tuple]] = []

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield _FakeConn(self)

    async def fetchval(self, sql, *args):
        return await _FakeConn(self).fetchval(sql, *args)

    def events(self, event_type: str) -> list[tuple]:
        return [a for sql, a in self.executed if "INSERT INTO bid_events" in sql and a[0] == event_type]

    def stat_bumps(self) -> list[str]:
        return [sql.split("supply_partner_id, ")[1].split(", spend)")[0]
                for sql, _ in self.executed if "INSERT INTO stats_hourly" in sql]


def _row(sp="p1", **over):
    row = {
        "bid_id": "b0", "imp_id": "1", "campaign_id": "c1", "line_item_id": "li1", "creative_id": "cr1",
        "supply_partner_id": sp, "device_type": "desktop", "geo_country": "RU", "ad_type": "banner",
        "domain": "site.ru", "os": "linux", "browser": "chrome", "bundle": None, "publisher_id": None,
        "tagid": None, "geo_region": None, "geo_city": None, "price": 100.0, "is_test": 0,
        "advertiser_id": "adv1", "reserve": 0.1, "has_total": 0, "has_li": 1,
        "business_date": cache.business_date_str(), "fdev": "", "fcaps": [], "fwin": 0,
    }
    row.update(over)
    return row


class _Harness:
    def __init__(self, monkeypatch, mode: str, row: dict, bill: BillResult | None = None):
        self.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        cache._client = self.redis
        self.pool = _FakePool(mode)
        self.row = row
        self.bill_calls: list[dict] = []
        harness = self
        bill = bill or BillResult(billed=True, cost=Decimal("0.08"), balance_after=Decimal(9), advertiser_id="adv1", paused=False)

        async def fake_bill_win(conn, **kw):
            harness.bill_calls.append(kw)
            if len(harness.bill_calls) > 1:
                return BillResult(billed=False, cost=Decimal(0), balance_after=None, advertiser_id=None, paused=False)
            return bill

        async def fake_find_bid(_pool, b):
            return dict(harness.row) if b == harness.row["bid_id"] else None

        async def fake_get_pool():
            return harness.pool

        monkeypatch.setattr(rtb_module, "bill_win", fake_bill_win)
        monkeypatch.setattr(rtb_module, "_find_bid", fake_find_bid)
        monkeypatch.setattr(rtb_module, "get_pool", fake_get_pool)
        monkeypatch.setattr(rtb_module, "get_redis", lambda: harness.redis)
        monkeypatch.setattr(rtb_module, "verify_event_token", lambda t: t)
        rtb_module.reset_billing_event_cache()
        obs_metrics.reset_for_tests()

    async def reserve(self):
        assert await gate_and_reserve(
            "c1", "li1", 0.1, 1000.0, 1000.0, bid_id="b0", advertiser_id="adv1",
            adv_balance=10.0, total_remaining=0.0, has_total=False,
        )

    async def avail(self) -> float:
        return float(await self.redis.get(avail_adv_key("adv1")) or 0)


def _counter(name: str) -> float:
    return obs_metrics.snapshot()["counters"][name]


# --- billing_event=win: как до R-07 -------------------------------------------

def test_win_mode_bills_on_nurl_and_ignores_burl(monkeypatch):
    async def run():
        h = _Harness(monkeypatch, "win", _row())
        await h.reserve()
        r = await rtb_module.win_notice(bid="b0", price="80")
        assert r == {"ok": True}
        assert len(h.bill_calls) == 1 and float(h.bill_calls[0]["cost"]) == pytest.approx(0.08)
        win = h.pool.events("win")
        assert len(win) == 1 and float(win[0][8]) == pytest.approx(80.0)  # clear_price на win
        assert h.pool.stat_bumps() == ["wins, billable_imps"]
        assert await h.redis.get(bid_state_key("b0")) == "win"
        # burl от партнёра в режиме win: ничего не пишем, только сигнал
        r = await rtb_module.billing_notice(bid="b0", price="80")
        assert r["ignored"] == "billing_event=win"
        assert len(h.bill_calls) == 1 and h.pool.events("billable") == []
        assert _counter("rtb_burl_in_win_mode_total") == 1
    asyncio.run(run())


# --- billing_event=burl ---------------------------------------------------------

def test_burl_mode_nurl_holds_reserve_and_burl_bills_once(monkeypatch):
    async def run():
        h = _Harness(monkeypatch, "burl", _row())
        await h.reserve()
        assert await h.avail() == pytest.approx(9.9)

        r = await rtb_module.win_notice(bid="b0", price="80")
        assert r == {"ok": True, "pending_burl": True}
        assert h.bill_calls == []
        win = h.pool.events("win")
        assert len(win) == 1 and win[0][8] is None  # win без clear_price: деньги на burl
        assert h.pool.stat_bumps() == ["wins"]
        assert await h.redis.get(bid_state_key("b0")) == "won"
        assert await h.redis.zscore(PENDING_BURL_KEY, "b0") is not None
        assert await h.avail() == pytest.approx(9.9)  # резерв держится
        assert _counter("rtb_win_pending_burl_total") == 1
        assert obs_metrics.snapshot()["burl_outcomes"][("p1", "won")] == 1

        # duplicate nurl — ничего
        assert await rtb_module.win_notice(bid="b0", price="80") == {"ok": True, "duplicate": True}
        assert len(h.pool.events("win")) == 1

        # burl: списание, billable, конвертация резерва
        r = await rtb_module.billing_notice(bid="b0", price="80")
        assert r == {"ok": True, "late": False, "paused": False}
        assert len(h.bill_calls) == 1 and float(h.bill_calls[0]["cost"]) == pytest.approx(0.08)
        billable = h.pool.events("billable")
        assert len(billable) == 1 and float(billable[0][8]) == pytest.approx(80.0) and billable[0][22] is False
        assert h.pool.stat_bumps() == ["wins", "billable_imps"]
        assert await h.redis.get(bid_state_key("b0")) == "win"
        assert await h.redis.zscore(PENDING_BURL_KEY, "b0") is None
        assert await h.redis.zscore(PENDING_BIDS_KEY, "b0") is None
        assert await h.avail() == pytest.approx(9.92)  # 10 - 0.08
        assert _counter("rtb_win_settle_converted_total") == 1 and _counter("rtb_burl_total") == 1
        assert obs_metrics.snapshot()["burl_outcomes"][("p1", "billed")] == 1

        # duplicate burl — bill_win говорит duplicate, событий и статистики нет
        assert await rtb_module.billing_notice(bid="b0", price="80") == {"ok": True, "duplicate": True}
        assert len(h.pool.events("billable")) == 1 and h.pool.stat_bumps() == ["wins", "billable_imps"]
        assert _counter("rtb_burl_duplicate_total") == 1
        assert await h.avail() == pytest.approx(9.92)
    asyncio.run(run())


def test_burl_mode_expire_then_late_burl(monkeypatch):
    async def run():
        h = _Harness(monkeypatch, "burl", _row())
        await h.reserve()
        await rtb_module.win_notice(bid="b0", price="80")
        # дедлайн прошёл
        await h.redis.zadd(PENDING_BURL_KEY, {"b0": time.time() - 1})
        out = await rtb_module.sweep_pending_burl()
        assert out["due"] == 1 and out["expired"] == 1 and out["reserve_missing"] == 0
        assert h.pool.events("win_expired") and h.bill_calls == []
        assert await h.redis.get(bid_state_key("b0")) == "won_expired"
        assert await h.avail() == pytest.approx(10.0)  # резерв вернулся, денег нет
        assert h.pool.stat_bumps() == ["wins"]
        assert _counter("rtb_win_without_burl_total") == 1
        assert obs_metrics.snapshot()["burl_outcomes"][("p1", "expired")] == 1
        assert obs_metrics.snapshot()["gauges"]["rtb_pending_burl_size"] == 0

        # повторный sweep — пусто
        out = await rtb_module.sweep_pending_burl()
        assert out["due"] == 0 and len(h.pool.events("win_expired")) == 1

        # поздний burl: списать ровно один раз, late=true
        r = await rtb_module.billing_notice(bid="b0", price="80")
        assert r["late"] is True
        assert len(h.bill_calls) == 1
        billable = h.pool.events("billable")
        assert len(billable) == 1 and billable[0][22] is True
        assert await h.avail() == pytest.approx(9.92)
        assert _counter("rtb_burl_late_total") == 1
        assert _counter("rtb_win_settle_recharged_total") == 1
        assert obs_metrics.snapshot()["burl_outcomes"][("p1", "late")] == 1
        # expire после burl ничего не двигает
        await h.redis.zadd(PENDING_BURL_KEY, {"b0": time.time() - 1})
        out = await rtb_module.sweep_pending_burl()
        assert out["already_billed"] == 1 and out["expired"] == 0
        assert await h.avail() == pytest.approx(9.92)
    asyncio.run(run())


def test_burl_without_nurl_bills_and_counts(monkeypatch):
    async def run():
        h = _Harness(monkeypatch, "burl", _row())
        await h.reserve()
        r = await rtb_module.billing_notice(bid="b0", price="80")
        assert r["late"] is False and len(h.bill_calls) == 1
        assert _counter("rtb_burl_without_nurl_total") == 1
        assert await h.avail() == pytest.approx(9.92)
    asyncio.run(run())


def test_burl_mode_cpc_and_test_bids_never_bill(monkeypatch):
    async def run():
        h = _Harness(monkeypatch, "burl", _row(bid_type="cpc", bid_cpc=5.0))
        r = await rtb_module.billing_notice(bid="b0", price="80")
        assert r == {"ok": True, "cpc": True} and h.bill_calls == []
        billable = h.pool.events("billable")
        assert len(billable) == 1 and billable[0][8] is None
        assert h.pool.stat_bumps() == ["billable_imps"]

        h2 = _Harness(monkeypatch, "burl", _row(is_test=1))
        r = await rtb_module.billing_notice(bid="b0", price="80")
        assert r == {"ok": True, "test": True} and h2.bill_calls == []
        assert h2.pool.events("billable")[0][21] is True and h2.pool.stat_bumps() == []
    asyncio.run(run())


def test_sweep_drops_orphans_without_bidmeta(monkeypatch):
    async def run():
        h = _Harness(monkeypatch, "burl", _row())
        await h.redis.zadd(PENDING_BURL_KEY, {"ghost": time.time() - 1})
        out = await rtb_module.sweep_pending_burl()
        assert out["orphans"] == 1 and out["expired"] == 0
        assert await h.redis.zscore(PENDING_BURL_KEY, "ghost") is None
    asyncio.run(run())


def test_billing_event_lookup_is_cached_and_defaults_to_win(monkeypatch):
    async def run():
        h = _Harness(monkeypatch, "burl", _row())
        assert await rtb_module._partner_billing_event(h.pool, "p1") == "burl"
        h.pool.mode = "win"
        assert await rtb_module._partner_billing_event(h.pool, "p1") == "burl"  # кэш
        rtb_module.reset_billing_event_cache()
        assert await rtb_module._partner_billing_event(h.pool, "p1") == "win"
        assert await rtb_module._partner_billing_event(h.pool, None) == "win"

        class _Broken:
            async def fetchval(self, *_):
                raise RuntimeError("no column billing_event")

        rtb_module.reset_billing_event_cache()
        assert await rtb_module._partner_billing_event(_Broken(), "p2") == "win"
    asyncio.run(run())


def test_bid_response_carries_burl_with_same_token():
    import inspect

    src = inspect.getsource(rtb_module)
    assert '/api/rtb/burl?bid={event_token}&price=${{AUCTION_PRICE}}' in src
    assert '"burl": burl,' in src
