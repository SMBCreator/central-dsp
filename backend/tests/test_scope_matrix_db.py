"""Матрица scope: роль × сущность × операция (audit S-02/S-03, план B12).

Спецификация — docs/audit/scope_spec.md. Здесь проверяется контракт
list → detail → allowed action для каждой роли на своих и чужих объектах:

* платформенные роли (superadmin/admin/analyst/finance) видят чужие объекты
  и в списке, и в карточке; admin/superadmin могут их менять, analyst/finance —
  нет (403 от guard, не 404 от scope);
* finance/admin проводят деньги любому рекламодателю и видят весь биллинг;
* agency видит и меняет объекты своих клиентов, клиент — только свои;
* viewer в CRUD не видит ничего (его граница — отчёты);
* ссылочные поля body (group/pixel/goal/profile/counterparty/contract/...)
  принадлежат владельцу главного объекта — иначе 404 и ничего не меняется.
  Это касается и admin-а: объекты двух разных владельцев в одной цепочке — 404.

Требует DATABASE_URL; сущности создаются и удаляются за собой (users CASCADE).
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
import routers.billing as billr  # noqa: E402
import routers.campaigns as campr  # noqa: E402
import routers.creatives as crs  # noqa: E402
import routers.groups as grpr  # noqa: E402
import routers.line_items as lir  # noqa: E402
import routers.ord_router as ordr  # noqa: E402
import routers.scoring as scor  # noqa: E402
from auth import current_user  # noqa: E402

BANNER = {"name": "x", "type": "banner", "width": 300, "height": 250,
          "asset_url": "http://a/i.png", "click_url": "http://x"}


class _Tenant:
    """Пользователь с полным деревом объектов: рекламодатель, кампания, группа
    кампаний, позиция, креатив, два контрагента, договор, два пикселя, цель,
    профиль скоринга."""

    def __init__(self, tag: str, role: str = "manager", parent: str | None = None):
        self.tag, self.role, self.parent = tag, role, parent
        self.ids: dict[str, str] = {}

    async def setup(self, conn):
        uid = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, role, parent_user_id) VALUES ($1,'x',$2,$3,$4) RETURNING id",
            f"scope-{self.tag}-{uuid.uuid4()}@test.local", self.tag, self.role, self.parent,
        )
        adv = await conn.fetchval(
            "INSERT INTO advertisers (user_id, name, status, balance) VALUES ($1,$2,'active',1000) RETURNING id",
            uid, f"adv-{self.tag}",
        )
        grp = await conn.fetchval(
            "INSERT INTO campaign_groups (user_id, name) VALUES ($1,$2) RETURNING id", uid, f"grp-{self.tag}"
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
        cp2 = await conn.fetchval(
            """INSERT INTO counterparties (user_id, name, inn, legal_type, role, ord_status)
               VALUES ($1,$2,$3,'ul','ors','registered') RETURNING id""",
            uid, f"cp2-{self.tag}", f"78{uuid.uuid4().int % 10**8:08d}",
        )
        contract = await conn.fetchval(
            """INSERT INTO ord_contracts (user_id, client_id, contractor_id, number, signed_at)
               VALUES ($1,$2,$3,$4, current_date) RETURNING id""",
            uid, cp, cp2, f"N-{self.tag}",
        )
        px = await conn.fetchval(
            "INSERT INTO pixels (user_id, name, domain) VALUES ($1,$2,'example.com') RETURNING id", uid, f"px-{self.tag}"
        )
        px2 = await conn.fetchval(
            "INSERT INTO pixels (user_id, name, domain) VALUES ($1,$2,'example.org') RETURNING id", uid, f"px2-{self.tag}"
        )
        goal = await conn.fetchval(
            "INSERT INTO goals (pixel_id, name, match_value) VALUES ($1,$2,'/thanks') RETURNING id", px, f"goal-{self.tag}"
        )
        profile = await conn.fetchval(
            "INSERT INTO scoring_profiles (user_id, name) VALUES ($1,$2) RETURNING id", uid, f"prof-{self.tag}"
        )
        self.ids.update(
            user=str(uid), adv=str(adv), grp=str(grp), camp=str(camp), li=str(li), cr=str(cr),
            cp=str(cp), cp2=str(cp2), contract=str(contract), px=str(px), px2=str(px2),
            goal=str(goal), profile=str(profile),
        )
        return self

    def as_user(self, permissions: dict | None = None) -> dict:
        return {"id": self.ids["user"], "email": f"{self.tag}@x", "name": self.tag, "role": self.role,
                "status": "active", "permissions": permissions or {}}

    async def snapshot(self, conn) -> dict:
        out = {}
        for key, table in (("adv", "advertisers"), ("camp", "campaigns"), ("li", "line_items"),
                           ("cr", "creatives"), ("profile", "scoring_profiles"), ("grp", "campaign_groups")):
            row = await conn.fetchrow(f"SELECT * FROM {table} WHERE id = $1", self.ids[key])
            out[key] = dict(row) if row else None
        return out

    async def teardown(self, conn):
        await _delete_user(conn, self.ids["user"])


async def _delete_user(conn, uid: str) -> None:
    """transactions — append-only (триггер запрещает и DELETE, и UPDATE от
    ON DELETE SET NULL): как в test_money_ops, снимаем триггер на время уборки."""
    await conn.execute("ALTER TABLE transactions DISABLE TRIGGER trg_transactions_append_only")
    try:
        await conn.execute(
            """DELETE FROM transactions
               WHERE user_id = $1 OR advertiser_id IN (SELECT id FROM advertisers WHERE user_id = $1)""",
            uid,
        )
    finally:
        await conn.execute("ALTER TABLE transactions ENABLE TRIGGER trg_transactions_append_only")
    await conn.execute("DELETE FROM users WHERE id = $1", uid)


async def _plain_user(conn, role: str, parent: str | None = None) -> dict:
    """Пользователь без собственных объектов (admin/analyst/finance/viewer)."""
    uid = await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role, parent_user_id) VALUES ($1,'x',$2,$3,$4) RETURNING id",
        f"scope-{role}-{uuid.uuid4()}@test.local", role, role, parent,
    )
    return {"id": str(uid), "email": f"{role}@x", "name": role, "role": role, "status": "active", "permissions": {}}


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
    for r in (advr.router, campr.router, lir.router, crs.router, grpr.router, scor.router, ordr.router, billr.router):
        app.include_router(r)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _ids(payload) -> set[str]:
    items = payload if isinstance(payload, list) else payload.get("items", payload)
    return {str(x.get("id")) for x in items}


# ---------------------------------------------------------------------------
# Платформенные роли: list и detail согласованы.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["superadmin", "admin", "analyst", "finance"])
def test_platform_role_sees_foreign_objects_in_list_and_detail(env, role):
    async def run():
        conn = await _conn()
        alice = await _Tenant("alice").setup(conn)
        me = await _plain_user(conn, role)
        try:
            async with _app_as(me) as c:
                checks = [
                    ("/campaigns", "camp"), ("/advertisers", "adv"), ("/groups", "grp"),
                    ("/scoring/profiles", "profile"), ("/ord/counterparties", "cp"), ("/ord/contracts", "contract"),
                ]
                for path, key in checks:
                    r = await c.get(path)
                    assert r.status_code == 200, f"{path}: {r.text}"
                    assert alice.ids[key] in _ids(r.json()), f"{role}: {path} не содержит объект alice"
                r = await c.get("/line-items", params={"campaign_id": alice.ids["camp"]})
                assert r.status_code == 200 and alice.ids["li"] in _ids(r.json())
                r = await c.get("/creatives", params={"campaign_id": alice.ids["camp"]})
                assert r.status_code == 200 and alice.ids["cr"] in _ids(r.json())
                # То, что видно в списке, открывается в карточке (S-03: superadmin
                # получал «Кампания не найдена»).
                for path in (f"/campaigns/{alice.ids['camp']}", f"/campaigns/{alice.ids['camp']}/diagnostics",
                             f"/campaigns/{alice.ids['camp']}/blocks", f"/advertisers/{alice.ids['adv']}"):
                    r = await c.get(path)
                    assert r.status_code == 200, f"{role}: {path} -> {r.status_code} {r.text}"
        finally:
            await alice.teardown(conn)
            await _delete_user(conn, me["id"])
            await conn.close()

    _run(run())


@pytest.mark.parametrize("role,expected", [("superadmin", 200), ("admin", 200), ("analyst", 403), ("finance", 403)])
def test_platform_role_write_on_foreign_object(env, role, expected):
    """admin/superadmin меняют чужое; analyst/finance получают 403 от guard —
    не 404 от scope (иначе read-only роль не отличить от «не найдено»)."""
    async def run():
        conn = await _conn()
        alice = await _Tenant("alice").setup(conn)
        me = await _plain_user(conn, role)
        try:
            async with _app_as(me) as c:
                r = await c.patch(f"/campaigns/{alice.ids['camp']}", json={"name": "renamed-by-platform"})
                assert r.status_code == expected, r.text
                if expected == 200:
                    # Владелец не меняется: объект остаётся у alice.
                    assert r.json()["user_id"] == alice.ids["user"]
                    r = await c.post("/campaigns", json={"advertiser_id": alice.ids["adv"], "name": "for-alice",
                                                         "group_id": alice.ids["grp"]})
                    assert r.status_code == 201, r.text
                    assert r.json()["user_id"] == alice.ids["user"]
                    r = await c.post("/line-items", json={"campaign_id": alice.ids["camp"], "name": "li", "bid_price": 1})
                    assert r.status_code == 201, r.text
                    assert r.json()["user_id"] == alice.ids["user"]
                    r = await c.post("/creatives", json={**BANNER, "campaign_id": alice.ids["camp"],
                                                         "counterparty_id": alice.ids["cp"],
                                                         "contract_id": alice.ids["contract"]})
                    assert r.status_code == 201, r.text
                    assert r.json()["user_id"] == alice.ids["user"]
                    r = await c.post("/ord/counterparties", json={"name": "cp", "inn": "7700000002",
                                                                  "advertiser_id": alice.ids["adv"]})
                    assert r.status_code == 201, r.text
                    assert r.json()["user_id"] == alice.ids["user"]
            name = await conn.fetchval("SELECT name FROM campaigns WHERE id = $1", alice.ids["camp"])
            assert (name == "renamed-by-platform") == (expected == 200)
        finally:
            await alice.teardown(conn)
            await _delete_user(conn, me["id"])
            await conn.close()

    _run(run())


# ---------------------------------------------------------------------------
# Billing: платёжная роль — вся платформа; остальные — свой scope.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["finance", "admin"])
def test_billing_platform_scope(env, role):
    async def run():
        conn = await _conn()
        alice, bob = await _Tenant("alice").setup(conn), await _Tenant("bob").setup(conn)
        me = await _plain_user(conn, role)
        try:
            async with _app_as(me) as c:
                r = await c.post("/billing/deposit", json={"advertiser_id": alice.ids["adv"], "amount": "250.00"})
                assert r.status_code == 201, r.text
                assert r.json()["balance"] == 1250.0
                r = await c.get("/billing/summary")
                assert r.status_code == 200
                advs = {a["id"] for a in r.json()["advertisers"]}
                assert {alice.ids["adv"], bob.ids["adv"]} <= advs
                assert r.json()["total_deposited"] >= 250.0
                r = await c.get("/billing/transactions", params={"advertiser_id": alice.ids["adv"]})
                assert r.status_code == 200 and any(t["amount"] == 250.0 for t in r.json())
            # Владелец видит проводку, проведённую платформенной ролью; сосед — нет.
            async with _app_as(alice.as_user({"can_view_billing": True})) as c:
                r = await c.get("/billing/transactions")
                assert r.status_code == 200 and any(t["amount"] == 250.0 for t in r.json()), r.text
                r = await c.get("/billing/summary")
                assert {a["id"] for a in r.json()["advertisers"]} == {alice.ids["adv"]}
                assert r.json()["total_deposited"] == 250.0
            async with _app_as(bob.as_user()) as c:
                r = await c.get("/billing/transactions")
                assert r.status_code == 200 and r.json() == []
                r = await c.get("/billing/summary")
                assert {a["id"] for a in r.json()["advertisers"]} == {bob.ids["adv"]}
                assert r.json()["total_deposited"] == 0.0
        finally:
            await alice.teardown(conn)
            await bob.teardown(conn)
            await _delete_user(conn, me["id"])
            await conn.close()

    _run(run())


# ---------------------------------------------------------------------------
# Agency: поддерево клиентов; клиент — только своё.
# ---------------------------------------------------------------------------
def test_agency_tree_scope(env):
    async def run():
        conn = await _conn()
        agency = await _Tenant("agency", role="agency").setup(conn)
        client = await _Tenant("client", role="client", parent=agency.ids["user"]).setup(conn)
        other = await _Tenant("other", role="client", parent=agency.ids["user"]).setup(conn)
        stranger = await _Tenant("stranger").setup(conn)
        try:
            async with _app_as(agency.as_user()) as c:
                r = await c.get("/campaigns")
                ids = _ids(r.json())
                assert {agency.ids["camp"], client.ids["camp"], other.ids["camp"]} <= ids
                assert stranger.ids["camp"] not in ids
                r = await c.get(f"/campaigns/{client.ids['camp']}")
                assert r.status_code == 200, r.text
                r = await c.patch(f"/campaigns/{client.ids['camp']}", json={"name": "by-agency"})
                assert r.status_code == 200 and r.json()["user_id"] == client.ids["user"]
                r = await c.post("/campaigns", json={"advertiser_id": client.ids["adv"], "name": "new-for-client"})
                assert r.status_code == 201 and r.json()["user_id"] == client.ids["user"], r.text
                r = await c.get(f"/campaigns/{stranger.ids['camp']}")
                assert r.status_code == 404
                # Цепочка: рекламодатель клиента + группа другого клиента — 404,
                # хотя оба в scope агентства.
                r = await c.post("/campaigns", json={"advertiser_id": client.ids["adv"], "name": "mixed",
                                                     "group_id": other.ids["grp"]})
                assert r.status_code == 404, r.text
                r = await c.get("/billing/summary")
                assert r.status_code == 200
                assert {a["id"] for a in r.json()["advertisers"]} >= {agency.ids["adv"], client.ids["adv"]}
                assert stranger.ids["adv"] not in {a["id"] for a in r.json()["advertisers"]}
            async with _app_as(client.as_user({"can_edit_campaigns": True})) as c:
                for camp in (agency.ids["camp"], other.ids["camp"], stranger.ids["camp"]):
                    r = await c.get(f"/campaigns/{camp}")
                    assert r.status_code == 404, f"client видит {camp}"
                    r = await c.patch(f"/campaigns/{camp}", json={"name": "pwned"})
                    assert r.status_code == 404
                r = await c.get("/campaigns")
                # Своя + созданная агентством «для клиента» (owner = client), чужих нет.
                assert client.ids["camp"] in _ids(r.json())
                assert {x["user_id"] for x in r.json()} == {client.ids["user"]}
        finally:
            for t in (client, other, stranger, agency):
                await t.teardown(conn)
            await conn.close()

    _run(run())


# ---------------------------------------------------------------------------
# Viewer: в CRUD пусто; отчёты — отдельный scope (report_scope_for).
# ---------------------------------------------------------------------------
def test_viewer_has_no_crud_scope(env):
    async def run():
        conn = await _conn()
        alice = await _Tenant("alice").setup(conn)
        viewer = await _plain_user(conn, "viewer")
        await conn.execute(
            "INSERT INTO guest_access (user_id, owner_id, advertiser_id) VALUES ($1,$2,$3)",
            viewer["id"], alice.ids["user"], alice.ids["adv"],
        )
        try:
            async with _app_as(viewer) as c:
                for path in ("/campaigns", "/advertisers", "/line-items", "/creatives", "/groups",
                             "/ord/counterparties", "/ord/contracts"):
                    r = await c.get(path)
                    assert r.status_code == 200 and _ids(r.json()) == set(), f"viewer видит {path}: {r.text}"
                for path in (f"/campaigns/{alice.ids['camp']}", f"/advertisers/{alice.ids['adv']}"):
                    r = await c.get(path)
                    assert r.status_code == 404, f"viewer открыл {path}"
                r = await c.get("/billing/summary")
                assert r.status_code == 403
        finally:
            await alice.teardown(conn)
            await _delete_user(conn, viewer["id"])
            await conn.close()

    _run(run())


# ---------------------------------------------------------------------------
# Ссылочные поля body: владелец главного объекта, иначе 404 и без изменений.
# ---------------------------------------------------------------------------
FOREIGN_REFS = [
    ("PATCH", "/campaigns/{a.camp}", {"group_id": "{b.grp}"}),
    ("PATCH", "/campaigns/{a.camp}", {"pixel_id": "{b.px}"}),
    ("PATCH", "/campaigns/{a.camp}", {"goal_id": "{b.goal}"}),
    ("PATCH", "/campaigns/{a.camp}", {"scoring_profile_id": "{b.profile}"}),
    ("POST", "/campaigns", {"advertiser_id": "{a.adv}", "name": "x", "group_id": "{b.grp}"}),
    ("PATCH", "/creatives/{a.cr}", {"counterparty_id": "{b.cp}"}),
    ("PATCH", "/creatives/{a.cr}", {"contract_id": "{b.contract}"}),
    ("POST", "/creatives", {**BANNER, "campaign_id": "{a.camp}", "contract_id": "{b.contract}"}),
    ("POST", "/creatives", {**BANNER, "campaign_id": "{a.camp}", "line_item_id": "{b.li}"}),
    ("POST", "/scoring/profiles", {"name": "p", "goal_id": "{b.goal}"}),
    ("POST", "/scoring/profiles", {"name": "p", "pixel_id": "{b.px}"}),
    ("PATCH", "/scoring/profiles/{a.profile}", {"pixel_id": "{b.px}"}),
    ("POST", "/ord/counterparties", {"name": "cp", "inn": "7700000001", "advertiser_id": "{b.adv}"}),
    ("POST", "/ord/contracts", {"client_id": "{a.cp}", "contractor_id": "{b.cp2}", "number": "1", "signed_at": "2026-01-01"}),
    ("POST", "/ord/contracts", {"client_id": "{a.cp}", "contractor_id": "{a.cp2}", "number": "1", "signed_at": "2026-01-01",
                                "parent_contract_id": "{b.contract}"}),
    ("POST", "/ord/creatives/{a.cr}/register", {"contract_id": "{b.contract}"}),
]


def _fill(obj, a: _Tenant, b: _Tenant):
    def one(v):
        if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
            who, key = v[1:-1].split(".")
            return (a if who == "a" else b).ids[key]
        return v
    if isinstance(obj, dict):
        return {k: one(v) for k, v in obj.items()}
    return one(obj)


def _fill_path(path: str, a: _Tenant, b: _Tenant) -> str:
    out = path
    for who, t in (("a", a), ("b", b)):
        for key, val in t.ids.items():
            out = out.replace(f"{{{who}.{key}}}", val)
    return out


@pytest.mark.parametrize("actor", ["owner", "admin"])
@pytest.mark.parametrize("method,path,body", FOREIGN_REFS, ids=[f"{m} {p} {list(b)[-1]}" for m, p, b in FOREIGN_REFS])
def test_foreign_reference_is_404_and_untouched(env, actor, method, path, body):
    """alice (владелец) и admin (всё в scope) — оба получают 404 на чужую ссылку:
    для admin это несовместимость цепочки (владелец объекта ≠ владелец ссылки)."""
    if actor == "admin" and path == "/ord/counterparties":
        # У контрагента нет главного объекта: admin заводит его клиенту (bob) —
        # это разрешено, владелец = владелец рекламодателя (см. test ниже).
        pytest.skip("admin создаёт контрагента чужому рекламодателю — легитимно")

    async def run():
        conn = await _conn()
        alice, bob = await _Tenant("alice").setup(conn), await _Tenant("bob").setup(conn)
        admin = await _plain_user(conn, "admin")
        try:
            before_a, before_b = await alice.snapshot(conn), await bob.snapshot(conn)
            user = alice.as_user() if actor == "owner" else admin
            async with _app_as(user) as c:
                r = await c.request(method, _fill_path(path, alice, bob), json=_fill(body, alice, bob))
            assert r.status_code == 404, f"{actor} {method} {path} -> {r.status_code}: {r.text}"
            assert "bob" not in r.text
            assert await alice.snapshot(conn) == before_a, "объект alice изменён"
            assert await bob.snapshot(conn) == before_b, "объект bob изменён"
            n = await conn.fetchval(
                "SELECT count(*) FROM ord_contracts WHERE user_id IN ($1, $2)", alice.ids["user"], bob.ids["user"]
            )
            assert n == 2, "создан договор с чужой стороной"
        finally:
            await alice.teardown(conn)
            await bob.teardown(conn)
            await _delete_user(conn, admin["id"])
            await conn.close()

    _run(run())


def test_own_references_are_accepted_and_goal_must_match_pixel(env):
    async def run():
        conn = await _conn()
        alice = await _Tenant("alice").setup(conn)
        try:
            async with _app_as(alice.as_user()) as c:
                r = await c.patch(f"/campaigns/{alice.ids['camp']}", json={
                    "group_id": alice.ids["grp"], "pixel_id": alice.ids["px"], "goal_id": alice.ids["goal"],
                    "scoring_profile_id": alice.ids["profile"],
                })
                assert r.status_code == 200, r.text
                d = r.json()
                assert (d["group_id"], d["pixel_id"], d["goal_id"], d["scoring_profile_id"]) == (
                    alice.ids["grp"], alice.ids["px"], alice.ids["goal"], alice.ids["profile"])
                # Цель другого пикселя — несовместимая цепочка (422, не 404: оба свои).
                r = await c.patch(f"/campaigns/{alice.ids['camp']}", json={"pixel_id": alice.ids["px2"]})
                assert r.status_code == 200, r.text
                r = await c.patch(f"/campaigns/{alice.ids['camp']}", json={"goal_id": alice.ids["goal"]})
                assert r.status_code == 422, r.text
                r = await c.patch(f"/campaigns/{alice.ids['camp']}", json={"pixel_id": alice.ids["px"],
                                                                          "goal_id": alice.ids["goal"]})
                assert r.status_code == 200, r.text
                r = await c.patch(f"/creatives/{alice.ids['cr']}", json={"counterparty_id": alice.ids["cp"],
                                                                        "contract_id": alice.ids["contract"]})
                assert r.status_code == 200, r.text
                r = await c.post("/ord/contracts", json={"client_id": alice.ids["cp"], "contractor_id": alice.ids["cp2"],
                                                         "number": "2", "signed_at": "2026-01-01",
                                                         "parent_contract_id": alice.ids["contract"]})
                assert r.status_code == 201, r.text
                assert r.json()["user_id"] == alice.ids["user"]
        finally:
            await alice.teardown(conn)
            await conn.close()

    _run(run())
