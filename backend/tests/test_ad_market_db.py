"""ТЗ п.5: рекламодатель «за пределами РФ» (advertisers.ad_market = 'foreign').

* API: поле в GET, принимается в POST (по умолчанию 'ru' для старых клиентов);
* 'foreign' + РФ-гео (страна, место РФ, пустое гео, geo_unknown=lenient) → 422
  при создании/изменении группы и при активации в обход UI;
* 'foreign' + KZ: активация без erid и ОРД-цепочки проходит, креатив в фиде;
* объекты 'foreign' не попадают в ОРД-выгрузку и раздел «Маркировка»;
* смена режима: только superadmin, 409 при сущностях в ОРД, запись в audit_log.
Требует DATABASE_URL; сущности создаются и удаляются тестом.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg  # noqa: E402
import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

import db  # noqa: E402
import routers.advertisers as advs  # noqa: E402
import routers.bidder_feed as feed  # noqa: E402
import routers.campaigns as camps  # noqa: E402
import routers.cron as cron_router  # noqa: E402
import routers.line_items as lis  # noqa: E402
import routers.ord_router as ordr  # noqa: E402
from auth import current_user  # noqa: E402

FEED_TOKEN = "feed-test-token"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("RTB_API_TOKEN", FEED_TOKEN)
    monkeypatch.delenv("ORD_OZON_API_KEY", raising=False)
    monkeypatch.delenv("ORD_FEED_GATE", raising=False)
    monkeypatch.delenv("LAUNCH_GATE", raising=False)
    monkeypatch.delenv("ORD_LAUNCH_GUARD", raising=False)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    db._pool = None
    yield
    db._pool = None


class _Env:
    def __init__(self):
        self.ids: dict[str, str] = {}

    async def setup(self, conn, *, market: str, geo: dict | None):
        uid = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','ad-market','admin') RETURNING id",
            f"ad-market-{uuid.uuid4()}@test.local",
        )
        adv = await conn.fetchval(
            """INSERT INTO advertisers (user_id, name, status, balance, ad_market)
               VALUES ($1,'adv','active',1000,$2) RETURNING id""",
            uid, market,
        )
        camp = await conn.fetchval(
            """INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget)
               VALUES ($1,$2,'camp','draft',100,0) RETURNING id""",
            adv, uid,
        )
        self.ids.update(user=str(uid), adv=str(adv), camp=str(camp))
        if geo is not None:
            li = await conn.fetchval(
                """INSERT INTO line_items (campaign_id, user_id, name, bid_price, status, targeting)
                   VALUES ($1,$2,'li',1.5,'active',$3::jsonb) RETURNING id""",
                camp, uid, json.dumps(geo),
            )
            cr = await conn.fetchval(
                """INSERT INTO creatives (campaign_id, line_item_id, user_id, name, type, width, height,
                                          asset_url, click_url, status)
                   VALUES ($1,$2,$3,'banner','banner',300,250,'http://a/i.png','http://x','approved') RETURNING id""",
                camp, li, uid,
            )
            self.ids.update(li=str(li), cr=str(cr))
        return self

    async def teardown(self, conn):
        await conn.execute("DELETE FROM users WHERE id = $1", self.ids["user"])


def _user(e: _Env, role: str = "admin") -> dict:
    return {"id": e.ids["user"], "email": "a@x", "name": "A", "role": role, "status": "active", "permissions": {}}


def _client(user_row: dict) -> httpx.AsyncClient:
    app = FastAPI()
    for r in (advs.router, camps.router, lis.router, ordr.router, feed.router, cron_router.router):
        app.include_router(r)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _feed_creatives(client: httpx.AsyncClient) -> set[str]:
    r = await client.get("/rtb-feed/campaigns", headers={"Authorization": f"Bearer {FEED_TOKEN}"})
    assert r.status_code == 200, r.text
    return {c["creative_id"] for c in r.json()["candidates"]}


def _run(scenario):
    async def wrapped():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await scenario(conn)
        finally:
            await conn.close()

    asyncio.run(wrapped())


# --------------------------------------------------------------------- API --
def test_api_default_ru_and_accepts_foreign(env):
    async def scenario(conn):
        e = await _Env().setup(conn, market="ru", geo=None)
        try:
            async with _client(_user(e)) as c:
                r = await c.post("/advertisers", json={"name": "Старый клиент"})
                assert r.status_code == 201, r.text
                assert r.json()["ad_market"] == "ru"
                r = await c.post("/advertisers", json={"name": "Зарубеж", "ad_market": "foreign"})
                assert r.status_code == 201, r.text
                assert r.json()["ad_market"] == "foreign"
                r = await c.post("/advertisers", json={"name": "X", "ad_market": "mars"})
                assert r.status_code == 422
                listed = {a["name"]: a["ad_market"] for a in (await c.get("/advertisers")).json()}
                assert listed["Зарубеж"] == "foreign" and listed["Старый клиент"] == "ru"
        finally:
            await e.teardown(conn)

    _run(scenario)


# --------------------------------------------------------------------- geo --
def test_foreign_rejects_ru_geo_on_line_item_create_and_patch(env):
    async def scenario(conn):
        e = await _Env().setup(conn, market="foreign", geo=None)
        await conn.execute(
            """INSERT INTO geo_places (id, country_iso, name_ru, name_en, name_ru_norm, name_en_norm, type, dataset_version)
               VALUES (990100002,'RU','Москва','Moscow','москва','moscow','city','test'),
                      (990100001,'KZ','Казахстан','Kazakhstan','казахстан','kazakhstan','country','test')
               ON CONFLICT (id) DO NOTHING"""
        )
        try:
            async with _client(_user(e)) as c:
                base = {"campaign_id": e.ids["camp"], "name": "g", "bid_type": "cpm", "bid_price": 1.0}
                bad = [
                    {"geo_countries": ["RU"]},
                    {"geo_countries": ["KZ", "RU"]},
                    {},
                    {"geo_place_ids": [990100002]},
                    {"geo_place_ids": [990100001], "geo_unknown": "lenient"},
                ]
                for t in bad:
                    r = await c.post("/line-items", json={**base, "targeting": t})
                    assert r.status_code == 422, (t, r.text)
                    assert "за пределами РФ" in r.json()["detail"]
                r = await c.post("/line-items", json={**base, "targeting": {"geo_place_ids": [990100001]}})
                assert r.status_code == 201, r.text
                li = r.json()["id"]
                r = await c.patch(f"/line-items/{li}", json={"targeting": {"geo_countries": ["RU"]}})
                assert r.status_code == 422, r.text
                r = await c.patch(f"/line-items/{li}", json={"targeting": {"geo_countries": ["DE"]}})
                assert r.status_code == 200, r.text
        finally:
            await e.teardown(conn)

    _run(scenario)


def test_ru_advertiser_keeps_ru_geo(env):
    async def scenario(conn):
        e = await _Env().setup(conn, market="ru", geo=None)
        try:
            async with _client(_user(e)) as c:
                r = await c.post("/line-items", json={
                    "campaign_id": e.ids["camp"], "name": "g", "bid_type": "cpm", "bid_price": 1.0,
                    "targeting": {"geo_countries": ["RU"]},
                })
                assert r.status_code == 201, r.text
        finally:
            await e.teardown(conn)

    _run(scenario)


def test_foreign_kz_activates_without_erid_and_reaches_feed(env):
    async def scenario(conn):
        e = await _Env().setup(conn, market="foreign", geo={"geo_countries": ["KZ"]})
        try:
            async with _client(_user(e)) as c:
                diag = (await c.get(f"/campaigns/{e.ids['camp']}/diagnostics")).json()
                codes = {b["code"] for b in diag["launch_blockers"]}
                assert not codes & {"ord_chain", "ord_marking", "ad_market_geo"}, diag["launch_blockers"]
                r = await c.patch(f"/campaigns/{e.ids['camp']}", json={"status": "active"})
                assert r.status_code == 200, r.text
                assert e.ids["cr"] in await _feed_creatives(c)
                # подтверждать цепочку ОРД для «за пределами РФ» нечего
                r = await c.post(f"/campaigns/{e.ids['camp']}/ord-chain/confirm")
                assert r.status_code == 409
        finally:
            await e.teardown(conn)

    _run(scenario)


def test_foreign_activation_with_ru_geo_bypassing_ui_is_422(env):
    async def scenario(conn):
        e = await _Env().setup(conn, market="foreign", geo={"geo_countries": ["RU"]})  # вставка в обход API
        try:
            async with _client(_user(e)) as c:
                diag = (await c.get(f"/campaigns/{e.ids['camp']}/diagnostics")).json()
                assert "ad_market_geo" in {b["code"] for b in diag["launch_blockers"]}
                r = await c.patch(f"/campaigns/{e.ids['camp']}", json={"status": "active"})
                assert r.status_code == 422, r.text
                r = await c.patch(f"/line-items/{e.ids['li']}", json={"status": "active"})
                assert r.status_code == 422, r.text
        finally:
            await e.teardown(conn)

    _run(scenario)


# --------------------------------------------------------------------- ОРД --
def test_foreign_excluded_from_ord_export_and_marking(env):
    period = datetime.now(timezone.utc).strftime("%Y-%m")

    async def scenario(conn):
        e = await _Env().setup(conn, market="foreign", geo={"geo_countries": ["KZ"]})
        try:
            await conn.execute(
                """INSERT INTO stats_hourly (ts_hour, campaign_id, line_item_id, creative_id, bids, wins,
                                             impressions, clicks, spend)
                   VALUES (date_trunc('hour', now()), $1, $2, $3, 100, 50, 50, 1, 1.5)""",
                e.ids["camp"], e.ids["li"], e.ids["cr"],
            )
            # исторические записи ОРД (если были) не должны светиться в «Маркировке»
            await conn.execute(
                "INSERT INTO ord_creatives (creative_id, ord_provider, status) VALUES ($1, 'mock', 'pending')",
                e.ids["cr"],
            )
            cp = await conn.fetchval(
                """INSERT INTO counterparties (user_id, advertiser_id, name, inn, legal_type, role)
                   VALUES ($1,$2,'ООО Зарубеж','7700000099','ul','advertiser') RETURNING id""",
                e.ids["user"], e.ids["adv"],
            )
            await conn.execute("DELETE FROM ord_export_log WHERE period = $1", period)
            async with _client(_user(e)) as c:
                r = await c.get("/cron/ord-export", params={"period": period})
                assert r.status_code == 200, r.text
                skipped = {x["creative_id"] for x in r.json()["skipped_without_erid"]}
                assert e.ids["cr"] not in skipped
                ord_crs = {x["creative_id"] for x in (await c.get("/ord/creatives")).json()}
                assert e.ids["cr"] not in {str(x) for x in ord_crs}
                cps = {str(x["id"]) for x in (await c.get("/ord/counterparties")).json()}
                assert str(cp) not in cps
        finally:
            await conn.execute("DELETE FROM ord_export_log WHERE period = $1", period)
            await e.teardown(conn)

    _run(scenario)


# ----------------------------------------------------------- смена режима --
def test_change_market_rules_and_audit(env):
    async def scenario(conn):
        e = await _Env().setup(conn, market="ru", geo={"geo_countries": ["KZ"]})
        adv = e.ids["adv"]
        try:
            async with _client(_user(e, "admin")) as c:
                r = await c.patch(f"/advertisers/{adv}", json={"ad_market": "foreign"})
                assert r.status_code == 403, r.text
                # тот же режим — не смена, админу можно сохранять карточку
                r = await c.patch(f"/advertisers/{adv}", json={"ad_market": "ru", "name": "adv2"})
                assert r.status_code == 200, r.text

            cp = await conn.fetchval(
                """INSERT INTO counterparties (user_id, advertiser_id, name, inn, legal_type, role, ord_status, ord_external_id)
                   VALUES ($1,$2,'ООО Рег','7700000098','ul','advertiser','registered','CP-X') RETURNING id""",
                e.ids["user"], adv,
            )
            async with _client(_user(e, "superadmin")) as c:
                r = await c.patch(f"/advertisers/{adv}", json={"ad_market": "foreign"})
                assert r.status_code == 409, r.text
                assert "ОРД" in r.json()["detail"]
                assert await conn.fetchval("SELECT ad_market FROM advertisers WHERE id = $1", adv) == "ru"

                await conn.execute("DELETE FROM counterparties WHERE id = $1", cp)
                r = await c.patch(f"/advertisers/{adv}", json={"ad_market": "foreign"})
                assert r.status_code == 200, r.text
                assert r.json()["ad_market"] == "foreign"
                audit = await conn.fetchrow(
                    "SELECT payload FROM audit_log WHERE entity = 'advertisers' AND entity_id = $1 ORDER BY id DESC LIMIT 1",
                    adv,
                )
                payload = json.loads(audit["payload"]) if isinstance(audit["payload"], str) else audit["payload"]
                assert payload == {"field": "ad_market", "from": "ru", "to": "foreign"}

                # обратно: мешает группа с РФ-гео, появившаяся в обход ограничений
                await conn.execute(
                    "UPDATE line_items SET targeting = '{\"geo_countries\": [\"RU\"]}'::jsonb WHERE id = $1", e.ids["li"]
                )
                r = await c.patch(f"/advertisers/{adv}", json={"ad_market": "ru"})
                assert r.status_code == 409, r.text
                await conn.execute(
                    "UPDATE line_items SET targeting = '{\"geo_countries\": [\"KZ\"]}'::jsonb WHERE id = $1", e.ids["li"]
                )
                r = await c.patch(f"/advertisers/{adv}", json={"ad_market": "ru"})
                assert r.status_code == 200, r.text
        finally:
            await e.teardown(conn)

    _run(scenario)
