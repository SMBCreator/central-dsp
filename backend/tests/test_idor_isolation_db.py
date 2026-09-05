"""IDOR: изоляция данных между менеджерами (требует DATABASE_URL).

Два независимых менеджера — alice и bob. Любое обращение alice к объекту bob
по прямому id должно давать 404 (не 403 — не раскрываем сам факт существования),
ничего не менять в строках bob и не утекать полями. Списки и статистика alice
не содержат объектов bob. Создание дочерних сущностей с чужим родителем
(campaign_id / advertiser_id / line_item_id / counterparty_id / pixel_id)
отклоняется.

Тесты создают свои сущности и удаляют их за собой (users ON DELETE CASCADE).
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
import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

import db  # noqa: E402
import routers.advertisers as advr  # noqa: E402
import routers.audiences_router as audr  # noqa: E402
import routers.campaigns as campr  # noqa: E402
import routers.creatives as crs  # noqa: E402
import routers.line_items as lir  # noqa: E402
import routers.rules as rulr  # noqa: E402
import routers.stats as statr  # noqa: E402
from auth import current_user  # noqa: E402


class _Tenant:
    """Менеджер со полным набором объектов: рекламодатель, кампания, группа,
    креатив, контрагент, пиксель, сегмент, правило и час статистики."""

    def __init__(self, tag: str):
        self.tag = tag
        self.ids: dict[str, str] = {}

    async def setup(self, conn):
        uid = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x',$2,'manager') RETURNING id",
            f"idor-{self.tag}-{uuid.uuid4()}@test.local", self.tag,
        )
        adv = await conn.fetchval(
            "INSERT INTO advertisers (user_id, name, status, balance) VALUES ($1,$2,'active',1000) RETURNING id",
            uid, f"adv-{self.tag}",
        )
        camp = await conn.fetchval(
            """INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget)
               VALUES ($1,$2,$3,'paused',100,0) RETURNING id""",
            adv, uid, f"camp-{self.tag}",
        )
        li = await conn.fetchval(
            """INSERT INTO line_items (campaign_id, user_id, name, bid_price, status, targeting)
               VALUES ($1,$2,$3,1.5,'active',$4::jsonb) RETURNING id""",
            camp, uid, f"li-{self.tag}", json.dumps({}),
        )
        cr = await conn.fetchval(
            """INSERT INTO creatives (campaign_id, line_item_id, user_id, name, type, width, height,
                                      asset_url, click_url, status)
               VALUES ($1,$2,$3,$4,'banner',300,250,'http://a/i.png','http://x','pending') RETURNING id""",
            camp, li, uid, f"cr-{self.tag}",
        )
        cp = await conn.fetchval(
            """INSERT INTO counterparties (user_id, advertiser_id, name, inn, legal_type, role, ord_status)
               VALUES ($1,$2,$3,$4,'ul','advertiser','registered') RETURNING id""",
            uid, adv, f"cp-{self.tag}", f"77{uuid.uuid4().int % 10**8:08d}",
        )
        px = await conn.fetchval(
            "INSERT INTO pixels (user_id, name, domain) VALUES ($1,$2,'example.com') RETURNING id",
            uid, f"px-{self.tag}",
        )
        seg = await conn.fetchval(
            """INSERT INTO audience_segments (user_id, pixel_id, name, rule_type, ttl_days, is_active)
               VALUES ($1,$2,$3,'all_visitors',30,true) RETURNING id""",
            uid, px, f"seg-{self.tag}",
        )
        rule = await conn.fetchval(
            """INSERT INTO rules (user_id, campaign_id, name, metric, operator, threshold, action, window_hours, status)
               VALUES ($1,$2,$3,'ctr','lt',0.1,'pause_campaign',24,'active') RETURNING id""",
            uid, camp, f"rule-{self.tag}",
        )
        await conn.execute(
            """INSERT INTO stats_hourly (ts_hour, campaign_id, line_item_id, creative_id, bids, wins, impressions,
                                         clicks, spend)
               VALUES (date_trunc('hour', now()), $1, $2, $3, 1000, 500, 500, 10, 12.5)""",
            camp, li, cr,
        )
        self.ids.update(
            user=str(uid), adv=str(adv), camp=str(camp), li=str(li), cr=str(cr),
            cp=str(cp), px=str(px), seg=str(seg), rule=str(rule),
        )
        return self

    def as_user(self) -> dict:
        return {"id": self.ids["user"], "email": f"{self.tag}@x", "name": self.tag, "role": "manager",
                "status": "active", "permissions": {}}

    async def snapshot(self, conn) -> dict:
        """Слепок строк для проверки «ничего не изменилось»."""
        out = {}
        for key, table in (("adv", "advertisers"), ("camp", "campaigns"), ("li", "line_items"),
                           ("cr", "creatives"), ("seg", "audience_segments"), ("rule", "rules")):
            row = await conn.fetchrow(f"SELECT * FROM {table} WHERE id = $1", self.ids[key])
            out[key] = dict(row) if row else None
        return out

    async def teardown(self, conn):
        await conn.execute("DELETE FROM users WHERE id = $1", self.ids["user"])


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("LAUNCH_GATE", "off")
    db._pool = None
    yield
    db._pool = None


def _run(coro):
    return asyncio.run(coro)


async def _conn():
    return await asyncpg.connect(DATABASE_URL)


def _app_as(user_row: dict) -> httpx.AsyncClient:
    app = FastAPI()
    for r in (advr.router, campr.router, lir.router, crs.router, audr.router, rulr.router, statr.router):
        app.include_router(r)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _two_tenants(conn):
    alice = await _Tenant("alice").setup(conn)
    bob = await _Tenant("bob").setup(conn)
    return alice, bob


# (method, path-template with {key}, json body) — alice обращается к объекту bob.
FOREIGN_CALLS = [
    ("GET", "/advertisers/{adv}", None),
    ("PATCH", "/advertisers/{adv}", {"name": "pwned"}),
    ("DELETE", "/advertisers/{adv}", None),
    ("GET", "/campaigns/{camp}", None),
    ("PATCH", "/campaigns/{camp}", {"name": "pwned"}),
    ("PATCH", "/campaigns/{camp}", {"status": "active"}),
    ("DELETE", "/campaigns/{camp}", None),
    ("POST", "/campaigns/{camp}/duplicate", None),
    ("GET", "/campaigns/{camp}/diagnostics", None),
    ("GET", "/campaigns/{camp}/blocks", None),
    ("PATCH", "/line-items/{li}", {"bid_price": 999}),
    ("DELETE", "/line-items/{li}", None),
    ("PUT", "/line-items/{li}/creatives", {"creative_ids": []}),
    ("PATCH", "/creatives/{cr}", {"name": "pwned"}),
    ("DELETE", "/creatives/{cr}", None),
    ("PATCH", "/audiences/{seg}", {"name": "pwned"}),
    ("DELETE", "/audiences/{seg}", None),
    ("PATCH", "/rules/{rule}", {"name": "pwned"}),
    ("DELETE", "/rules/{rule}", None),
]


@pytest.mark.parametrize("method,path,body", FOREIGN_CALLS, ids=[f"{m} {p}" for m, p, _ in FOREIGN_CALLS])
def test_foreign_object_is_404_and_untouched(env, method, path, body):
    async def run():
        conn = await _conn()
        alice, bob = await _two_tenants(conn)
        try:
            before = await bob.snapshot(conn)
            url = path.format(**bob.ids)
            async with _app_as(alice.as_user()) as c:
                r = await c.request(method, url, json=body)
            assert r.status_code == 404, f"{method} {url} -> {r.status_code}: {r.text}"
            # Ни одного поля чужого объекта в ответе (имя bob уникально по тегу).
            assert "bob" not in r.text
            after = await bob.snapshot(conn)
            assert after == before, f"{method} {url} изменил данные bob"
        finally:
            await alice.teardown(conn)
            await bob.teardown(conn)
            await conn.close()

    _run(run())


def test_lists_hide_foreign_rows(env):
    async def run():
        conn = await _conn()
        alice, bob = await _two_tenants(conn)
        try:
            async with _app_as(alice.as_user()) as c:
                for path in ("/advertisers", "/campaigns", "/line-items", "/creatives", "/audiences", "/rules"):
                    r = await c.get(path)
                    assert r.status_code == 200, f"{path}: {r.text}"
                    payload = r.json()
                    items = payload if isinstance(payload, list) else payload.get("items", payload)
                    text = json.dumps(items, ensure_ascii=False, default=str)
                    assert "-bob" not in text, f"{path} отдаёт объекты bob"
                    for bid in bob.ids.values():
                        assert bid not in text, f"{path} содержит id bob {bid}"
                # Фильтр списка по чужой кампании — пусто, не 200 с чужими данными.
                r = await c.get("/creatives", params={"campaign_id": bob.ids["camp"]})
                assert r.status_code == 200 and r.json() == []
                r = await c.get("/line-items", params={"campaign_id": bob.ids["camp"]})
                assert r.status_code == 200
                assert not [x for x in (r.json() if isinstance(r.json(), list) else []) if x.get("id") == bob.ids["li"]]
        finally:
            await alice.teardown(conn)
            await bob.teardown(conn)
            await conn.close()

    _run(run())


def test_stats_filtered_by_foreign_campaign_are_zero(env):
    async def run():
        conn = await _conn()
        alice, bob = await _two_tenants(conn)
        try:
            async with _app_as(alice.as_user()) as c:
                r = await c.get("/stats/summary", params={"campaign_id": bob.ids["camp"], "hours": 48})
                assert r.status_code == 200, r.text
                s = r.json()
                assert float(s.get("impressions", 0)) == 0 and float(s.get("spend", 0)) == 0, s
                r = await c.get("/stats/timeseries", params={"campaign_id": bob.ids["camp"], "hours": 48})
                assert r.status_code == 200, r.text
                pts = r.json()
                pts = pts if isinstance(pts, list) else pts.get("points", pts.get("items", []))
                assert all(float(p.get("impressions", 0)) == 0 for p in pts), pts
                r = await c.get("/stats/breakdown", params={"by": "campaign", "hours": 48})
                assert r.status_code in (200, 422), r.text
                if r.status_code == 200:
                    assert bob.ids["camp"] not in r.text
        finally:
            await alice.teardown(conn)
            await bob.teardown(conn)
            await conn.close()

    _run(run())


def test_create_with_foreign_parent_is_rejected(env):
    async def run():
        conn = await _conn()
        alice, bob = await _two_tenants(conn)
        try:
            async with _app_as(alice.as_user()) as c:
                r = await c.post("/campaigns", json={"advertiser_id": bob.ids["adv"], "name": "x"})
                assert r.status_code == 404, r.text
                r = await c.post("/line-items", json={"campaign_id": bob.ids["camp"], "name": "x", "bid_price": 1})
                assert r.status_code == 404, r.text
                r = await c.post("/rules", json={"campaign_id": bob.ids["camp"], "name": "x", "metric": "ctr",
                                                 "operator": "lt", "threshold": 0.1, "action": "pause_campaign"})
                assert r.status_code == 404, r.text
                r = await c.post("/audiences", json={"pixel_id": bob.ids["px"], "name": "x",
                                                     "rule_type": "all_visitors"})
                assert r.status_code == 404, r.text
                base = {"name": "x", "type": "banner", "width": 300, "height": 250,
                        "asset_url": "http://a/i.png", "click_url": "http://x"}
                r = await c.post("/creatives", json={**base, "campaign_id": bob.ids["camp"]})
                assert r.status_code == 404, r.text
                # Своя кампания, но чужая группа / чужой контрагент.
                r = await c.post("/creatives", json={**base, "campaign_id": alice.ids["camp"],
                                                     "line_item_id": bob.ids["li"]})
                assert r.status_code == 404, r.text
                r = await c.post("/creatives", json={**base, "campaign_id": alice.ids["camp"],
                                                     "counterparty_id": bob.ids["cp"]})
                assert r.status_code == 404, r.text
            # Ничего из этого не появилось у bob.
            n = await conn.fetchval("SELECT count(*) FROM creatives WHERE line_item_id = $1", bob.ids["li"])
            assert n == 1
        finally:
            await alice.teardown(conn)
            await bob.teardown(conn)
            await conn.close()

    _run(run())


def test_attach_foreign_creative_to_own_line_item_is_noop(env):
    """PUT /line-items/{own}/creatives с чужим creative_id не должен перевесить чужой креатив."""
    async def run():
        conn = await _conn()
        alice, bob = await _two_tenants(conn)
        try:
            async with _app_as(alice.as_user()) as c:
                r = await c.put(f"/line-items/{alice.ids['li']}/creatives", json={"creative_ids": [bob.ids["cr"]]})
                # Чужой id для СВОЕЙ группы — это ошибка запроса (400), не «не найдено».
                assert r.status_code == 400, r.text
            li_of_bob_cr = await conn.fetchval("SELECT line_item_id::text FROM creatives WHERE id = $1", bob.ids["cr"])
            assert li_of_bob_cr == bob.ids["li"], "чужой креатив перевешен на группу alice"
        finally:
            await alice.teardown(conn)
            await bob.teardown(conn)
            await conn.close()

    _run(run())
