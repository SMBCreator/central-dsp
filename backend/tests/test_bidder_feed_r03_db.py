"""Контракт /rtb-feed/campaigns для Go-биддера (audit R-03, требует DATABASE_URL).

Всё, что Python-биддер применяет из БД на каждом запросе — блоки оптимизатора,
scoring-блоклист, «грязные» домены для clean traffic, objective/clean_traffic_only
кампании — должно уезжать в фид, иначе Go эти настройки молча игнорирует.
"""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg  # noqa: E402
from fastapi import FastAPI  # noqa: E402
import httpx  # noqa: E402

import db  # noqa: E402
import routers.bidder_feed as feed  # noqa: E402

FEED_TOKEN = "feed-r03-token"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("RTB_API_TOKEN", FEED_TOKEN)
    monkeypatch.delenv("ORD_FEED_GATE", raising=False)
    db._pool = None
    yield
    db._pool = None


async def _seed(conn, *, clean_traffic: bool):
    uid = await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','feed-r03','admin') RETURNING id",
        f"feed-r03-{uuid.uuid4()}@test.local",
    )
    adv = await conn.fetchval(
        "INSERT INTO advertisers (user_id, name, status, balance) VALUES ($1,'adv','active',1000) RETURNING id", uid
    )
    camp = await conn.fetchval(
        """INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget,
                                  objective, clean_traffic_only)
           VALUES ($1,$2,'camp','active',100,0,'traffic',$3) RETURNING id""",
        adv, uid, clean_traffic,
    )
    li = await conn.fetchval(
        """INSERT INTO line_items (campaign_id, user_id, name, bid_price, status, targeting)
           VALUES ($1,$2,'li',1.5,'active',$3::jsonb) RETURNING id""",
        camp, uid, json.dumps({"geo_countries": ["US"]}),  # не РФ: erid не требуется
    )
    await conn.execute(
        """INSERT INTO creatives (campaign_id, user_id, name, type, width, height, asset_url, click_url, status)
           VALUES ($1,$2,'b','banner',300,250,'http://a/i.png','http://x','approved')""",
        camp, uid,
    )
    await conn.execute(
        """INSERT INTO optimizer_blocks (campaign_id, block_type, block_value, reason)
           VALUES ($1,'domain','Bad.Example','ctr'), ($1,'os','ios','cpa')""",
        camp,
    )
    await conn.execute(
        "INSERT INTO scoring_blocklist (campaign_id, domain, score) VALUES ($1,'low.example',0.1)", camp
    )
    await conn.execute(
        """INSERT INTO domain_quality (domain, score) VALUES ('dirty.example', 0.1), ('clean.example', 0.9)
           ON CONFLICT (domain) DO UPDATE SET score = EXCLUDED.score"""
    )
    return {"user": str(uid), "camp": str(camp), "li": str(li)}


async def _fetch_feed():
    app = FastAPI()
    app.include_router(feed.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/rtb-feed/campaigns", headers={"Authorization": f"Bearer {FEED_TOKEN}"})
        assert r.status_code == 200, r.text
        return r.json()


def test_feed_carries_optimizer_scoring_and_dirty_domains(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        ids = await _seed(conn, clean_traffic=True)
        try:
            body = await _fetch_feed()
            cand = next(c for c in body["candidates"] if str(c["li_id"]) == ids["li"])
            assert cand["objective"] == "traffic"
            assert cand["clean_traffic_only"] is True

            blocks = {(b["block_type"], b["block_value"]) for b in body["optimizer_blocks"]
                      if b["campaign_id"] == ids["camp"]}
            assert blocks == {("domain", "Bad.Example"), ("os", "ios")}

            scoring = {b["domain"] for b in body["scoring_blocks"] if b["campaign_id"] == ids["camp"]}
            assert scoring == {"low.example"}

            assert "dirty.example" in body["dirty_domains"]
            assert "clean.example" not in body["dirty_domains"]
        finally:
            await conn.execute("DELETE FROM domain_quality WHERE domain IN ('dirty.example','clean.example')")
            await conn.execute("DELETE FROM users WHERE id = $1", ids["user"])
            await conn.close()

    asyncio.run(scenario())


def test_feed_carries_active_pctr_model(env):
    # Часть B: активная pCTR-модель едет в Go тем же отбором, что ml.pctr
    # (_load_active_model): последняя active-версия, global_ctr в (0,1), все
    # сегменты версии. Retired-версия и её сегменты не попадают.
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        ids = await _seed(conn, clean_traffic=False)  # objective=traffic, bid_type cpm -> модель нужна
        await conn.execute("UPDATE ml_models SET status = 'retired' WHERE model_type = 'pctr' AND status = 'active'")
        base = await conn.fetchval("SELECT coalesce(max(version), 0) FROM ml_models WHERE model_type = 'pctr'")
        v_old, v_new = base + 1, base + 2
        try:
            await conn.execute(
                """INSERT INTO ml_models (model_type, version, status, metrics)
                   VALUES ('pctr', $1, 'retired', '{"global_ctr": 0.05}'::jsonb),
                          ('pctr', $2, 'active',  '{"global_ctr": 0.01}'::jsonb)""",
                v_old, v_new,
            )
            await conn.execute(
                """INSERT INTO ml_pctr_segments (model_version, segment_type, segment_key, imps, clicks, ctr_smoothed)
                   VALUES ($1,'domain','old.example',100,5,0.05),
                          ($2,'domain','pub.example',100,3,0.03), ($2,'hour','14',200,3,0.012)""",
                v_old, v_new,
            )
            body = await _fetch_feed()
            model = body["pctr_model"]
            assert model is not None
            assert model["version"] == v_new
            assert model["global_ctr"] == 0.01
            segs = {(s["type"], s["key"]): s["ctr"] for s in model["segments"]}
            assert segs == {("domain", "pub.example"): 0.03, ("hour", "14"): 0.012}

            # global_ctr вне (0,1) -> модель невалидна -> null (Go биддит базовой ценой).
            await conn.execute(
                "UPDATE ml_models SET metrics = '{\"global_ctr\": 1.0}'::jsonb WHERE model_type='pctr' AND version=$1", v_new
            )
            assert (await _fetch_feed())["pctr_model"] is None
        finally:
            await conn.execute("DELETE FROM ml_pctr_segments WHERE model_version IN ($1,$2)", v_old, v_new)
            await conn.execute("DELETE FROM ml_models WHERE model_type='pctr' AND version IN ($1,$2)", v_old, v_new)
            await conn.execute("DELETE FROM users WHERE id = $1", ids["user"])
            await conn.execute("DELETE FROM domain_quality WHERE domain IN ('dirty.example','clean.example')")
            await conn.close()

    asyncio.run(scenario())


def test_dirty_domains_omitted_when_no_campaign_opts_in(env):
    # Список «грязных» доменов может быть большим — отдаём его только если хоть
    # одна кампания в фиде включила clean_traffic_only.
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        # Убираем чужие opt-in кампании из выборки: тест изолируем по флагу.
        others = await conn.fetchval(
            """SELECT count(*) FROM line_items li JOIN campaigns c ON c.id = li.campaign_id
               WHERE c.clean_traffic_only AND c.status = 'active' AND li.status = 'active'"""
        )
        ids = await _seed(conn, clean_traffic=False)
        try:
            body = await _fetch_feed()
            if others == 0:
                assert body["dirty_domains"] == []
            cand = next(c for c in body["candidates"] if str(c["li_id"]) == ids["li"])
            assert cand["clean_traffic_only"] is False
        finally:
            await conn.execute("DELETE FROM domain_quality WHERE domain IN ('dirty.example','clean.example')")
            await conn.execute("DELETE FROM users WHERE id = $1", ids["user"])
            await conn.close()

    asyncio.run(scenario())


def test_feed_passes_context_keys_as_is_and_old_targeting_unchanged(env):
    # Контекст v2: ключи едут в Go как есть; у старой кампании JSON прежний (ничего не добавляется).
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        ids = await _seed(conn, clean_traffic=False)
        try:
            body = await _fetch_feed()
            cand = next(c for c in body["candidates"] if str(c["li_id"]) == ids["li"])
            assert cand["targeting"] == {"geo_countries": ["US"]}

            new = {
                "geo_countries": ["US"],
                "iab_categories": ["IAB1"],
                "iab_allow_unknown": True,
                "keywords_allow": ["ипотек"],
                "keywords_match": "word_start",
                "context_mode": "all",
            }
            await conn.execute("UPDATE line_items SET targeting = $1::jsonb WHERE id = $2", json.dumps(new), ids["li"])
            body = await _fetch_feed()
            cand = next(c for c in body["candidates"] if str(c["li_id"]) == ids["li"])
            assert cand["targeting"] == new
        finally:
            await conn.execute("DELETE FROM users WHERE id = $1", ids["user"])
            await conn.close()

    asyncio.run(scenario())
