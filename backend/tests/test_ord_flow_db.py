"""Сквозной поток маркировки (требует DATABASE_URL, ОРД — mock).

approve модератора → авторегистрация в ОРД → erid на креативе → креатив в фиде.
Без erid креатив, показываемый в РФ, в фид не попадает; для не-РФ гео erid
не нужен. Тесты создают свои сущности и удаляют их за собой.
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
import routers.creatives as crs  # noqa: E402
import routers.moderation as mod  # noqa: E402
import routers.ord_router as ordr  # noqa: E402
from auth import current_user  # noqa: E402
from ord.service import campaign_targets_ru, register_creative, resolve_contract  # noqa: E402

FEED_TOKEN = "feed-test-token"


class _Env:
    """Один пользователь-админ, рекламодатель, кампания, группа, контрагент."""

    def __init__(self):
        self.ids: dict[str, str] = {}

    async def setup(self, conn, *, geo: list[str] | None):
        uid = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','ord-flow','admin') RETURNING id",
            f"ord-flow-{uuid.uuid4()}@test.local",
        )
        adv = await conn.fetchval(
            "INSERT INTO advertisers (user_id, name, status, balance) VALUES ($1,'adv','active',1000) RETURNING id", uid
        )
        camp = await conn.fetchval(
            """INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget)
               VALUES ($1,$2,'camp','active',100,0) RETURNING id""",
            adv, uid,
        )
        targeting = {"geo_countries": geo} if geo is not None else {}
        li = await conn.fetchval(
            """INSERT INTO line_items (campaign_id, user_id, name, bid_price, status, targeting)
               VALUES ($1,$2,'li',1.5,'active',$3::jsonb) RETURNING id""",
            camp, uid, json.dumps(targeting),
        )
        cp = await conn.fetchval(
            """INSERT INTO counterparties (user_id, advertiser_id, name, inn, legal_type, role, ord_status, ord_external_id)
               VALUES ($1,$2,'ООО Тест','7700000001','ul','advertiser','registered','CP-1') RETURNING id""",
            uid, adv,
        )
        ors = await conn.fetchval(
            """INSERT INTO counterparties (user_id, name, inn, legal_type, role, ord_status, ord_external_id)
               VALUES ($1,'Central DSP','7700000002','ul','ors','registered','CP-2') RETURNING id""",
            uid,
        )
        self.ids.update(user=str(uid), adv=str(adv), camp=str(camp), li=str(li), cp=str(cp), ors=str(ors))
        return self

    async def add_contract(self, conn, *, registered=True, number="Д-1"):
        cid = await conn.fetchval(
            """INSERT INTO ord_contracts (user_id, client_id, contractor_id, number, signed_at, ord_status, ord_external_id)
               VALUES ($1,$2,$3,$4,'2026-01-01',$5,$6) RETURNING id""",
            self.ids["user"], self.ids["cp"], self.ids["ors"], number,
            "registered" if registered else "pending", f"CT-{number}" if registered else None,
        )
        return str(cid)

    async def add_creative(self, conn, *, status="pending", counterparty=True, contract_id=None):
        cr = await conn.fetchval(
            """INSERT INTO creatives (campaign_id, user_id, name, type, width, height, asset_url, click_url,
                                      status, counterparty_id, contract_id, kktu_code)
               VALUES ($1,$2,'banner','banner',300,250,'http://a/i.png','http://x',$3,$4,$5,'30.10') RETURNING id""",
            self.ids["camp"], self.ids["user"], status,
            self.ids["cp"] if counterparty else None, contract_id,
        )
        return str(cr)

    async def teardown(self, conn):
        # users ON DELETE CASCADE тянет всё остальное
        await conn.execute("DELETE FROM users WHERE id = $1", self.ids["user"])


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("RTB_API_TOKEN", FEED_TOKEN)
    monkeypatch.delenv("ORD_OZON_API_KEY", raising=False)
    monkeypatch.delenv("ORD_FEED_GATE", raising=False)
    # каждый тест — свежий loop; сбрасываем пул, привязанный к прошлому loop
    db._pool = None
    yield
    db._pool = None


def _run(coro):
    return asyncio.run(coro)


async def _conn():
    return await asyncpg.connect(DATABASE_URL)


def _app_as(user_row: dict) -> httpx.AsyncClient:
    """HTTP-клиент к in-process приложению в ТОМ ЖЕ event loop, что и asyncpg
    (TestClient крутит свой loop — пул соединений к нему не привяжется)."""
    app = FastAPI()
    app.include_router(mod.router)
    app.include_router(ordr.router)
    app.include_router(feed.router)
    app.include_router(crs.router)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _admin(env_: _Env) -> dict:
    return {"id": env_.ids["user"], "email": "a@x", "name": "A", "role": "admin",
            "status": "active", "permissions": {}}


async def _feed_creatives(client: httpx.AsyncClient) -> set[str]:
    r = await client.get("/rtb-feed/campaigns", headers={"Authorization": f"Bearer {FEED_TOKEN}"})
    assert r.status_code == 200, r.text
    return {c["creative_id"] for c in r.json()["candidates"]}


# ---------------------------------------------------------------------------
def test_approve_registers_in_ord_and_creative_reaches_feed(env):
    async def scenario():
        conn = await _conn()
        e = await _Env().setup(conn, geo=["RU"])
        try:
            await e.add_contract(conn)
            cr = await e.add_creative(conn)
            client = _app_as(_admin(e))

            # до approve — pending, в фиде нет
            assert cr not in await _feed_creatives(client)

            r = await client.post(f"/moderation/{cr}/approve")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "approved"
            assert body["ord"]["status"] == "ok"
            assert body["erid"] and body["erid"].startswith("MOCK-")

            row = await conn.fetchrow("SELECT erid, contract_id FROM creatives WHERE id = $1", cr)
            assert row["erid"] == body["erid"]
            assert row["contract_id"] is not None
            oc = await conn.fetchrow("SELECT status, erid FROM ord_creatives WHERE creative_id = $1", cr)
            assert oc["status"] == "registered" and oc["erid"] == body["erid"]

            # после approve+erid — в фиде
            assert cr in await _feed_creatives(client)
        finally:
            await e.teardown(conn)
            await conn.close()

    _run(scenario())


def test_approve_without_contract_marks_awaiting_and_keeps_out_of_feed(env):
    async def scenario():
        conn = await _conn()
        e = await _Env().setup(conn, geo=None)  # пустое гео = весь мир = включает РФ
        try:
            cr = await e.add_creative(conn)  # контрагент есть, договора нет
            client = _app_as(_admin(e))
            r = await client.post(f"/moderation/{cr}/approve")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "approved"          # модерация прошла
            assert body["ord"]["status"] == "awaiting_contract"
            assert body["erid"] is None
            oc = await conn.fetchrow("SELECT status, error FROM ord_creatives WHERE creative_id = $1", cr)
            assert oc["status"] == "awaiting_contract" and "договор" in oc["error"].lower()
            # 38-ФЗ: в фид не попал
            assert cr not in await _feed_creatives(client)

            # Появился договор → ручная регистрация с страницы ОРД без contract_id подбирает его
            await e.add_contract(conn)
            r2 = await client.post(f"/ord/creatives/{cr}/register", json={})
            assert r2.status_code == 200, r2.text
            assert r2.json()["erid"].startswith("MOCK-")
            assert cr in await _feed_creatives(client)
        finally:
            await e.teardown(conn)
            await conn.close()

    _run(scenario())


def test_approve_without_kktu_marks_awaiting_kktu_and_keeps_out_of_feed(env):
    """ККТУ обязателен для регистрации в ОРД: без кода у креатива и у кампании
    approve проходит, но erid не выдаётся, в ОРД не ходим, в фид не попадаем.
    Ручная регистрация → 409 с понятной причиной. После заполнения кода на
    кампании (fallback) регистрация проходит."""
    async def scenario():
        conn = await _conn()
        e = await _Env().setup(conn, geo=["RU"])
        try:
            await e.add_contract(conn)
            cr = await e.add_creative(conn)
            await conn.execute("UPDATE creatives SET kktu_code = NULL WHERE id = $1", cr)
            await conn.execute("UPDATE campaigns SET kktu_code = NULL WHERE id = $1", e.ids["camp"])
            client = _app_as(_admin(e))

            r = await client.post(f"/moderation/{cr}/approve")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "approved"
            assert body["ord"]["status"] == "awaiting_kktu"
            assert body["erid"] is None
            oc = await conn.fetchrow("SELECT status, error, payload FROM ord_creatives WHERE creative_id = $1", cr)
            assert oc["status"] == "awaiting_kktu" and "ккту" in oc["error"].lower()
            assert oc["payload"] is None  # в ОРД ничего не отправляли
            assert cr not in await _feed_creatives(client)

            r2 = await client.post(f"/ord/creatives/{cr}/register", json={})
            assert r2.status_code == 409, r2.text
            assert "ККТУ" in r2.json()["detail"]

            # ККТУ на кампании — достаточный fallback
            await conn.execute("UPDATE campaigns SET kktu_code = '30.10' WHERE id = $1", e.ids["camp"])
            r3 = await client.post(f"/ord/creatives/{cr}/register", json={})
            assert r3.status_code == 200, r3.text
            assert r3.json()["erid"].startswith("MOCK-")
            oc2 = await conn.fetchrow("SELECT payload FROM ord_creatives WHERE creative_id = $1", cr)
            assert json.loads(oc2["payload"])["kktu"] == "30.10"
            assert cr in await _feed_creatives(client)
        finally:
            await e.teardown(conn)
            await conn.close()

    _run(scenario())


def test_non_ru_geo_does_not_require_erid(env):
    async def scenario():
        conn = await _conn()
        e = await _Env().setup(conn, geo=["KZ", "BY"])
        try:
            cr = await e.add_creative(conn, counterparty=False)
            client = _app_as(_admin(e))
            assert not await campaign_targets_ru(await db.get_pool(), e.ids["camp"])
            r = await client.post(f"/moderation/{cr}/approve")
            assert r.status_code == 200, r.text
            assert r.json()["ord"]["status"] == "not_required"
            assert r.json()["erid"] is None
            # без erid, но гео не-РФ → в фиде есть
            assert cr in await _feed_creatives(client)
        finally:
            await e.teardown(conn)
            await conn.close()

    _run(scenario())


def test_resolve_contract_is_ambiguous_with_two_contracts(env):
    async def scenario():
        conn = await _conn()
        e = await _Env().setup(conn, geo=["RU"])
        try:
            c1 = await e.add_contract(conn, number="Д-1")
            c2 = await e.add_contract(conn, number="Д-2")
            pool = await db.get_pool()
            cr_id = await e.add_creative(conn)
            cr = dict(await conn.fetchrow("SELECT * FROM creatives WHERE id = $1", cr_id))
            # два подходящих договора — не выбираем за пользователя
            assert await resolve_contract(pool, cr) is None
            res = await register_creative(pool, cr)
            assert res["outcome"] == "awaiting_contract"
            # явный выбор — регистрируем по нему
            res2 = await register_creative(pool, cr, contract_id=c2)
            assert res2["outcome"] == "ok"
            row = await conn.fetchrow("SELECT contract_id, erid FROM creatives WHERE id = $1", cr_id)
            assert str(row["contract_id"]) == c2 and row["erid"]
            assert c1 != c2
        finally:
            await e.teardown(conn)
            await conn.close()

    _run(scenario())


def test_register_is_idempotent(env):
    async def scenario():
        conn = await _conn()
        e = await _Env().setup(conn, geo=["RU"])
        try:
            await e.add_contract(conn)
            pool = await db.get_pool()
            cr_id = await e.add_creative(conn, status="approved")
            cr = dict(await conn.fetchrow("SELECT * FROM creatives WHERE id = $1", cr_id))
            a = await register_creative(pool, cr)
            b = await register_creative(pool, cr)
            assert a["outcome"] == b["outcome"] == "ok"
            assert a["erid"] == b["erid"]
        finally:
            await e.teardown(conn)
            await conn.close()

    _run(scenario())


def _manager(env_: _Env) -> dict:
    return {**_admin(env_), "role": "manager", "permissions": {"can_manage_creatives": True}}


def _banner_body(e: _Env, **over) -> dict:
    return {
        "campaign_id": e.ids["camp"], "line_item_id": e.ids["li"], "name": "b", "type": "banner",
        "width": 300, "height": 250, "asset_url": "http://a/1.png", "click_url": "http://x",
        "counterparty_id": e.ids["cp"], "kktu_code": "30.10", **over,
    }


def test_admin_created_creative_is_marked_without_moderation_queue(env):
    """Админ модерирует сам себя: креатив сразу approved — но не минуя ОРД."""
    async def scenario():
        conn = await _conn()
        e = await _Env().setup(conn, geo=["RU"])
        try:
            client = _app_as(_admin(e))
            # договора ещё нет → approved, awaiting_contract, не в фиде
            r = await client.post("/creatives", json=_banner_body(e))
            assert r.status_code == 201, r.text
            b0 = r.json()
            assert b0["status"] == "approved" and not b0.get("erid")
            assert b0["ord"]["status"] == "awaiting_contract"
            assert b0["id"] not in await _feed_creatives(client)

            await e.add_contract(conn)
            r = await client.post("/creatives", json=_banner_body(e, asset_url="http://a/2.png"))
            assert r.status_code == 201, r.text
            b1 = r.json()
            assert b1["status"] == "approved" and b1["erid"] and b1["ord"]["status"] == "ok"
            assert b1["id"] in await _feed_creatives(client)
        finally:
            await e.teardown(conn)
            await conn.close()

    _run(scenario())


def test_content_change_reissues_erid_for_admin_and_remoderates_for_manager(env):
    async def scenario():
        conn = await _conn()
        e = await _Env().setup(conn, geo=["RU"])
        try:
            await e.add_contract(conn)
            admin = _app_as(_admin(e))
            r = await admin.post("/creatives", json=_banner_body(e))
            cr = r.json()
            erid0 = cr["erid"]
            assert erid0

            # admin меняет картинку: статус остаётся approved, erid перевыдан
            r = await admin.patch(f"/creatives/{cr['id']}", json={"asset_url": "http://a/new.png"})
            assert r.status_code == 200, r.text
            ch = r.json()
            assert ch["status"] == "approved" and ch["ord"]["status"] == "ok"
            assert ch["erid"] and ch["erid"] != erid0
            assert cr["id"] in await _feed_creatives(admin)
            # B13: старая версия остаётся в истории со своим erid, текущая — registered
            hist = await conn.fetch(
                "SELECT version, status, erid FROM ord_creatives WHERE creative_id = $1 ORDER BY version", cr["id"]
            )
            assert [(h["version"], h["status"], h["erid"]) for h in hist] == [
                (1, "superseded", erid0), (2, "registered", ch["erid"]),
            ]

            # manager (тот же владелец) меняет картинку: pending, erid снят, superseded, вне фида
            mgr = _app_as(_manager(e))
            r = await mgr.patch(f"/creatives/{cr['id']}", json={"asset_url": "http://a/mgr.png"})
            assert r.status_code == 200, r.text
            m = r.json()
            assert m["status"] == "pending" and not m["erid"] and "ord" not in m
            assert cr["id"] not in await _feed_creatives(mgr)
            assert await conn.fetchval(
                "SELECT count(*) FROM ord_creatives WHERE creative_id = $1 AND superseded_at IS NULL", cr["id"]
            ) == 0

            # повторный approve → новый erid на новое содержимое
            r = await admin.post(f"/moderation/{cr['id']}/approve")
            assert r.status_code == 200, r.text
            assert r.json()["erid"] not in (None, erid0, ch["erid"])
        finally:
            await e.teardown(conn)
            await conn.close()

    _run(scenario())


def test_feed_gate_can_be_disabled_for_dev(env, monkeypatch):
    monkeypatch.setenv("ORD_FEED_GATE", "off")

    async def scenario():
        conn = await _conn()
        e = await _Env().setup(conn, geo=["RU"])
        try:
            cr = await e.add_creative(conn, status="approved", counterparty=False)
            client = _app_as(_admin(e))
            assert cr in await _feed_creatives(client)
        finally:
            await e.teardown(conn)
            await conn.close()

    _run(scenario())
