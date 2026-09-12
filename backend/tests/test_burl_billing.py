"""Биллинг по burl (audit R-07): win notice — не billable-событие.

State machine bidstate:{bid} для партнёра с billing_event=burl:
  (nil, резерв) --nurl--> won --burl--> win          резерв -> clearing
                          won --expire--> won_expired  резерв возвращён, денег нет
                          won_expired --burl--> win   clearing досписан (late)
Идемпотентность: повторный nurl/burl/expire ничего не двигают.
"""
import asyncio
import re
import time
from pathlib import Path

import fakeredis.aioredis
import pytest

import cache
import obs_metrics
from cache import (
    PENDING_BIDS_KEY,
    PENDING_BURL_KEY,
    avail_adv_key,
    bid_state_key,
    expire_burl,
    gate_and_reserve,
    pending_burl_due,
    pending_burl_stats,
    release_bid,
    res_adv_key,
    settle_win,
    spend_key,
    win_pending,
)


def _fresh_redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache._client = r
    return r


async def _f(r, key) -> float:
    v = await r.get(key)
    return float(v) if v is not None else 0.0


def _res(bid_id, reserve=1.0, caps=None, device_id=""):
    return {
        "bid_id": bid_id, "campaign_id": "c1", "line_item_id": "li1", "advertiser_id": "adv1",
        "reserve": reserve, "business_date": cache.business_date_str(),
        "has_total": False, "has_li": True, "caps": caps or [], "device_id": device_id,
    }


async def _bid(bid_id, cost=1.0):
    return await gate_and_reserve(
        "c1", "li1", cost, 1000.0, 1000.0, bid_id=bid_id, advertiser_id="adv1",
        adv_balance=5.0, total_remaining=0.0, has_total=False,
    )


# --- Lua: nurl держит резерв, burl конвертирует -------------------------------

def test_win_pending_keeps_reserve_and_extends_deadline():
    async def run():
        r = _fresh_redis()
        assert await _bid("b0")
        before = await r.zscore(PENDING_BIDS_KEY, "b0")
        assert await win_pending(_res("b0")) == 1
        assert await r.get(bid_state_key("b0")) == "won"
        # резерв не тронут: avail = 5 - 1, res = 1
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.0)
        assert await _f(r, res_adv_key("adv1")) == pytest.approx(1.0)
        # дневной spend-ключ несёт резерв целиком (как до burl) — расход не подтверждён, но и не «свободен»
        assert await _f(r, spend_key("camp", "c1")) == pytest.approx(1.0)
        # дедлайн в pending:bids сдвинут за BURL_TTL + grace, бид в pending:burl
        after = await r.zscore(PENDING_BIDS_KEY, "b0")
        assert after - before >= cache.BURL_TTL
        burl_deadline = await r.zscore(PENDING_BURL_KEY, "b0")
        assert burl_deadline <= after - cache.BURL_SWEEP_GRACE + 1
        # повторный nurl — no-op
        assert await win_pending(_res("b0")) == 0
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.0)
    asyncio.run(run())


def test_burl_after_pending_converts_reserve_and_refunds_difference():
    async def run():
        r = _fresh_redis()
        assert await _bid("b0")
        assert await win_pending(_res("b0")) == 1
        assert await settle_win(_res("b0"), 0.6) == 1
        assert await r.get(bid_state_key("b0")) == "win"
        assert await _f(r, spend_key("camp", "c1")) == pytest.approx(0.6)
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.4)
        assert await _f(r, res_adv_key("adv1")) == pytest.approx(0.0)
        assert await r.zscore(PENDING_BIDS_KEY, "b0") is None
        assert await r.zscore(PENDING_BURL_KEY, "b0") is None
        # повторный burl — ноль движения
        assert await settle_win(_res("b0"), 0.6) == 0
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.4)
        # nurl после burl (порядок наоборот) — тоже no-op
        assert await win_pending(_res("b0")) == 0
    asyncio.run(run())


def test_expire_refunds_reserve_and_late_burl_recharges_once():
    async def run():
        r = _fresh_redis()
        assert await _bid("b0")
        assert await win_pending(_res("b0")) == 1
        assert await expire_burl(_res("b0")) == 1
        assert await r.get(bid_state_key("b0")) == "won_expired"
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(5.0)
        assert await _f(r, res_adv_key("adv1")) == pytest.approx(0.0)
        assert await r.zscore(PENDING_BIDS_KEY, "b0") is None
        assert await r.zscore(PENDING_BURL_KEY, "b0") is None
        # повторный expire — ничего
        assert await expire_burl(_res("b0")) == 0
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(5.0)
        # поздний burl: clearing досписывается, код 3 (late)
        assert await settle_win(_res("b0"), 0.7) == 3
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.3)
        assert await _f(r, spend_key("camp", "c1")) == pytest.approx(0.7)
        # expire после burl — ничего не возвращает
        assert await expire_burl(_res("b0")) == 0
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.3)
    asyncio.run(run())


def test_expire_after_go_sweeper_took_reserve_reports_missing():
    """Sweeper Go (SET NX) не может вернуть резерв бида в состоянии won и лишь
    убирает его из pending:bids — expire_burl видит это и сообщает кодом 2."""
    async def run():
        r = _fresh_redis()
        assert await _bid("b0")
        assert await win_pending(_res("b0")) == 1
        await r.zrem(PENDING_BIDS_KEY, "b0")
        assert await expire_burl(_res("b0")) == 2
        assert await r.get(bid_state_key("b0")) == "won_expired"
        assert await settle_win(_res("b0"), 0.7) == 3
    asyncio.run(run())


def test_nurl_after_loss_release_then_burl_recharges():
    """Резерв уже снят loss-уведомлением, потом nurl (won без резерва), потом
    burl: конвертировать нечего — clearing досписывается (код 2, не 1)."""
    async def run():
        r = _fresh_redis()
        assert await _bid("b0")
        assert await release_bid(_res("b0"), "loss") is True
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(5.0)
        assert await win_pending(_res("b0")) == 2
        assert await settle_win(_res("b0"), 0.7) == 2
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.3)
    asyncio.run(run())


def test_burl_without_nurl_settles_like_a_win():
    async def run():
        r = _fresh_redis()
        assert await _bid("b0")
        assert await settle_win(_res("b0"), 0.5) == 1
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.5)
    asyncio.run(run())


def test_win_mode_settle_unchanged_by_r07():
    """billing_event=win: settle на nurl как до R-07 (регрессия M-06)."""
    async def run():
        r = _fresh_redis()
        assert await _bid("b0")
        assert await settle_win(_res("b0"), 0.6) == 1
        assert await release_bid(_res("b0"), "expired") is False
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.4)
    asyncio.run(run())


def test_freq_cap_step_runs_once_on_pending_not_again_on_burl():
    """R-06 внутри R-07: бронь freqinf -> w:{bid} делается на nurl; burl её не
    пересоздаёт (иначе после imp появилась бы фантомная бронь и ложный
    win_without_imp)."""
    async def run():
        r = _fresh_redis()
        caps = [(3, 24)]  # (limit, hours) — формат bidmeta fcaps
        res = _res("b0", caps=caps, device_id="dev1")
        assert await _bid("b0")
        inflight = cache.freq_inflight_key("c1", "dev1")
        await r.zadd(inflight, {"b0": time.time() + 90})
        assert await win_pending(res) == 1
        assert await r.zscore(inflight, "b0") is None
        assert await r.zscore(inflight, "w:b0") is not None
        await r.zrem(inflight, "w:b0")  # imp подтвердил
        assert await settle_win(res, 0.5) == 1
        assert await r.zscore(inflight, "w:b0") is None
    asyncio.run(run())


def test_pending_burl_due_and_stats():
    async def run():
        r = _fresh_redis()
        now = int(time.time())
        await r.zadd(PENDING_BURL_KEY, {"old": now - 10, "fresh": now + 500})
        assert await pending_burl_due(now) == ["old"]
        size, age = await pending_burl_stats(now)
        assert size == 2 and age == pytest.approx(cache.BURL_TTL + 10, abs=2)
    asyncio.run(run())


# --- Контракты -----------------------------------------------------------------

def test_reservation_keys_end_with_pending_burl():
    keys = cache._reservation_keys(_res("b0"))
    assert len(keys) == 13 and keys[-1] == PENDING_BURL_KEY
    assert keys[1] == PENDING_BIDS_KEY


def test_settle_lua_branches_on_won_state_and_pending_burl_key():
    lua = cache._SETTLE_WIN_LUA
    assert "prev == 'won'" in lua and "KEYS[13]" in lua
    assert "prev == 'won_expired' then return 3" in lua


def test_burl_ttl_defaults_and_env(monkeypatch):
    assert cache.DEFAULT_BURL_TTL == 600 and cache.BURL_SWEEP_GRACE > 0
    assert cache._env_positive_int("BURL_TTL_TEST_X", 600) == 600
    monkeypatch.setenv("BURL_TTL_TEST_X", "-5")
    assert cache._env_positive_int("BURL_TTL_TEST_X", 600) == 600


def test_migration_046_shape():
    sql = Path("migrations/046_billing_event_burl.sql").read_text(encoding="utf-8")
    # существующие партнёры остаются на win, новые — burl
    assert re.search(r"ADD COLUMN IF NOT EXISTS billing_event TEXT NOT NULL DEFAULT 'win'", sql)
    assert "ALTER COLUMN billing_event SET DEFAULT 'burl'" in sql
    assert "CHECK (billing_event IN ('win', 'burl'))" in sql
    assert "ADD COLUMN IF NOT EXISTS billable_imps INTEGER NOT NULL DEFAULT 0" in sql
    assert "ADD COLUMN IF NOT EXISTS late BOOLEAN NOT NULL DEFAULT false" in sql


def test_stats_math_billable_contract():
    from stats_math import (
        ADDITIVE_METRICS,
        BILLABLE_FROM_EVENTS_SQL,
        SPEND_FROM_EVENTS_SQL,
        derive,
    )

    assert "billable_imps" in ADDITIVE_METRICS
    assert "IN ('win', 'billable')" in SPEND_FROM_EVENTS_SQL
    assert "event_type = 'billable'" in BILLABLE_FROM_EVENTS_SQL and "clear_price IS NOT NULL" in BILLABLE_FROM_EVENTS_SQL
    d = derive(impressions=200, spend=10.0, billable_imps=100)
    assert d["cpm"] == pytest.approx(50.0) and d["ecpm"] == pytest.approx(100.0)
    d0 = derive(impressions=200, spend=10.0)
    assert d0["ecpm"] == d0["cpm"]


def test_cron_jobs_registered_for_every_cron_route():
    """@_cron_job(name) с именем вне CRON_JOBS -> KeyError после выполнения
    задачи (500 в cron). Ловили на reconcile_reserves/audience_sync."""
    src = Path("routers/cron.py").read_text(encoding="utf-8")
    names = set(re.findall(r'_cron_job\("([a-z_]+)"\)', src))
    assert names and names <= set(obs_metrics.CRON_JOBS), names - set(obs_metrics.CRON_JOBS)


def test_burl_outcome_metrics_render_per_partner():
    obs_metrics.reset_for_tests()
    obs_metrics.inc_burl_outcome("p1", "won")
    obs_metrics.inc_burl_outcome("p1", "won")
    obs_metrics.inc_burl_outcome("p1", "expired")
    obs_metrics.inc_burl_outcome(None, "billed")
    text = obs_metrics.render_prometheus()
    assert 'rtb_burl_outcomes_total{partner="p1",outcome="won"} 2' in text
    assert 'rtb_burl_outcomes_total{partner="p1",outcome="expired"} 1' in text
    assert 'rtb_burl_outcomes_total{partner="unknown",outcome="billed"} 1' in text
    with pytest.raises(KeyError):
        obs_metrics.inc_burl_outcome("p1", "bogus")
    for name in ("rtb_win_without_burl_total", "rtb_burl_late_total", "rtb_burl_in_win_mode_total"):
        obs_metrics.inc(name)
    obs_metrics.set_gauge("rtb_pending_burl_size", 3)
    obs_metrics.reset_for_tests()
    assert obs_metrics.snapshot()["burl_outcomes"] == {}
