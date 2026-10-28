"""ТЗ batch creatives п.5: серверная проверка размера баннера и POST /creatives/batch.

* POST /creatives: размер из справочника → 201; иначе 422 size_not_allowed;
  файл нашей загрузки другого размера → 422 size_mismatch; CREATIVE_SIZE_STRICT=0
  отключает проверку; PATCH «старого» размера и native/video не затронуты;
* POST /creatives/batch: все ок, частичный успех, лимит 30, идемпотентность по
  asset_url, чужая кампания — как у POST /creatives, foreign — без ОРД,
  РФ — статус ОРД по элементам, запись в audit_log.
Требует DATABASE_URL; сущности создаются и удаляются тестом.
"""
import asyncio
import json
import os
import struct
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
import main  # noqa: E402
import routers.creatives as crs  # noqa: E402
from auth import current_user  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.delenv("ORD_OZON_API_KEY", raising=False)
    monkeypatch.delenv("ORD_FEED_GATE", raising=False)
    monkeypatch.delenv("CREATIVE_SIZE_STRICT", raising=False)
    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path))
    db._pool = None
    yield tmp_path
    db._pool = None


def _run(coro):
    return asyncio.run(coro)


class _Tenant:
    def __init__(self):
        self.ids: dict[str, str] = {}

    async def setup(self, conn, *, role="admin", geo=("RU",), ad_market="ru"):
        uid = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','batch',$2) RETURNING id",
            f"batch-{uuid.uuid4()}@test.local", role,
        )
        adv = await conn.fetchval(
            """INSERT INTO advertisers (user_id, name, status, balance, ad_market)
               VALUES ($1,'adv','active',1000,$2) RETURNING id""",
            uid, ad_market,
        )
        camp = await conn.fetchval(
            """INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget)
               VALUES ($1,$2,'camp','active',100,0) RETURNING id""",
            adv, uid,
        )
        li = await conn.fetchval(
            """INSERT INTO line_items (campaign_id, user_id, name, bid_price, status, targeting)
               VALUES ($1,$2,'li',1.5,'active',$3::jsonb) RETURNING id""",
            camp, uid, json.dumps({"geo_countries": list(geo)}),
        )
        self.ids.update(user=str(uid), camp=str(camp), li=str(li))
        self.role = role
        return self

    def user(self, role: str | None = None) -> dict:
        role = role or self.role
        perms = {"can_manage_creatives": True} if role == "manager" else {}
        return {"id": self.ids["user"], "email": "b@x", "name": "B", "role": role,
                "status": "active", "permissions": perms}

    async def teardown(self, conn):
        await conn.execute("DELETE FROM users WHERE id = $1", self.ids["user"])


def _client(user: dict) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(crs.router)

    async def _fake_user():
        return user

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _banner(t: _Tenant, **over) -> dict:
    return {"campaign_id": t.ids["camp"], "line_item_id": t.ids["li"], "name": "b", "type": "banner",
            "width": 300, "height": 250, "asset_url": f"http://cdn.test/{uuid.uuid4().hex}.png",
            "click_url": "https://site.test/", "kktu_code": "30.10", **over}


def _png(path: Path, w: int, h: int) -> None:
    ihdr = struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00" * 64)


def _item(cid: str, w=300, h=250, **over) -> dict:
    return {"client_id": cid, "name": f"n-{cid}", "asset_url": f"http://cdn.test/{uuid.uuid4().hex}.png",
            "width": w, "height": h, **over}


def _batch(t: _Tenant, items: list[dict], **common) -> dict:
    return {"campaign_id": t.ids["camp"], "line_item_id": t.ids["li"],
            "common": {"click_url": "https://site.test/", "kktu_code": "30.10", **common}, "items": items}


# ------------------------------------------------------------- POST /creatives


def test_create_banner_allowed_size_and_rejects_unknown(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            async with _client(t.user()) as c:
                r = await c.post("/creatives", json=_banner(t, width=120, height=600))
                assert r.status_code == 201, r.text
                r = await c.post("/creatives", json=_banner(t, width=301, height=250))
                assert r.status_code == 422
                d = r.json()["detail"]
                assert d["code"] == "size_not_allowed"
                assert d["nearest"][0] == "300x250"
                r = await c.post("/creatives", json=_banner(t, width=20, height=600))
                assert r.status_code == 422 and r.json()["detail"]["code"] == "size_not_allowed"
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_create_banner_size_mismatch_with_uploaded_file(env):
    name = f"{uuid.uuid4().hex}.png"
    _png(env / name, 728, 90)

    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            async with _client(t.user()) as c:
                url = f"https://dsp.test/uploads/{name}"
                r = await c.post("/creatives", json=_banner(t, asset_url=url))
                assert r.status_code == 422
                d = r.json()["detail"]
                assert d["code"] == "size_mismatch" and d["actual"] == "728x90"
                r = await c.post("/creatives", json=_banner(t, asset_url=url, width=728, height=90))
                assert r.status_code == 201, r.text
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_size_strict_off_disables_check(env, monkeypatch):
    monkeypatch.setenv("CREATIVE_SIZE_STRICT", "0")

    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            async with _client(t.user()) as c:
                r = await c.post("/creatives", json=_banner(t, width=999, height=111))
                assert r.status_code == 201, r.text
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_patch_legacy_size_and_native_video_unaffected(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            legacy = await conn.fetchval(
                """INSERT INTO creatives (campaign_id, user_id, name, type, width, height, asset_url, click_url, status)
                   VALUES ($1,$2,'old','banner',999,111,'http://cdn.test/o.png','https://site.test/','approved')
                   RETURNING id""",
                t.ids["camp"], t.ids["user"],
            )
            async with _client(t.user()) as c:
                r = await c.patch(f"/creatives/{legacy}", json={"name": "renamed"})
                assert r.status_code == 200, r.text
                r = await c.post("/creatives", json={
                    "campaign_id": t.ids["camp"], "name": "nat", "type": "native", "native_title": "T",
                    "click_url": "https://site.test/", "kktu_code": "30.10",
                })
                assert r.status_code == 201, r.text
                r = await c.post("/creatives", json={
                    "campaign_id": t.ids["camp"], "name": "vid", "type": "video", "width": 640, "height": 360,
                    "video_url": "https://cdn.test/v.mp4", "video_duration": 15,
                    "click_url": "https://site.test/", "kktu_code": "30.10",
                })
                assert r.status_code == 201, r.text
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


# -------------------------------------------------------- POST /creatives/batch


def test_batch_all_ok_with_click_override_and_audit(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn, role="manager")
        try:
            async with _client(t.user()) as c:
                items = [_item("a", 300, 250), _item("b", 728, 90, click_url="https://other.test/b")]
                r = await c.post("/creatives/batch", json=_batch(t, items))
                assert r.status_code == 200, r.text
                res = {x["client_id"]: x for x in r.json()}
                assert res["a"]["status"] == res["b"]["status"] == "created"
                assert res["a"]["creative"]["status"] == "pending"
                assert res["a"]["creative"]["click_url"] == "https://site.test/"
                assert res["b"]["creative"]["click_url"] == "https://other.test/b"
                assert res["b"]["creative"]["name"] == "n-b"
                assert res["a"]["creative"]["line_item_id"] == t.ids["li"]
            row = await conn.fetchrow(
                "SELECT payload FROM audit_log WHERE path = '/creatives/batch' AND entity_id = $1 ORDER BY id DESC LIMIT 1",
                t.ids["camp"],
            )
            assert row is not None
            p = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            assert p["created"] == 2 and p["errors"] == 0 and p["items"] == 2
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_batch_partial_success(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            async with _client(t.user()) as c:
                items = [_item("ok", 300, 250), _item("bad", 301, 250), _item("rel", asset_url="/uploads/x.png")]
                r = await c.post("/creatives/batch", json=_batch(t, items))
                assert r.status_code == 200, r.text
                res = {x["client_id"]: x for x in r.json()}
                assert res["ok"]["status"] == "created"
                assert res["bad"]["status"] == "error" and res["bad"]["error"]["code"] == "size_not_allowed"
                assert res["rel"]["status"] == "error" and res["rel"]["error"]["code"] == "invalid"
            n = await conn.fetchval("SELECT count(*) FROM creatives WHERE campaign_id = $1", t.ids["camp"])
            assert n == 1
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_batch_item_pixels_override_inherit_and_isolate_errors(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            async with _client(t.user()) as c:
                items = [
                    _item("own", imp_tracker_url="https://own.test/i?cb=${CDSP_CACHEBUSTER}",
                          click_tracker_url="https://own.test/c"),
                    _item("inherit"),
                    _item("blank", imp_tracker_url="  ", click_tracker_url=""),
                    _item("bad", 728, 90, imp_tracker_url="javascript:alert(1)"),
                    _item("typo", 160, 600, click_tracker_url="https://own.test/c?x=${CDSP_COUNTY}"),
                ]
                r = await c.post("/creatives/batch", json=_batch(
                    t, items, imp_tracker_url="https://common.test/i", click_tracker_url="https://common.test/c"))
                assert r.status_code == 200, r.text
                res = {x["client_id"]: x for x in r.json()}
                own = res["own"]["creative"]
                assert own["imp_tracker_url"] == "https://own.test/i?cb=${CDSP_CACHEBUSTER}"
                assert own["click_tracker_url"] == "https://own.test/c"
                for cid in ("inherit", "blank"):
                    cr = res[cid]["creative"]
                    assert cr["imp_tracker_url"] == "https://common.test/i"
                    assert cr["click_tracker_url"] == "https://common.test/c"
                for cid in ("bad", "typo"):
                    assert res[cid]["status"] == "error" and res[cid]["error"]["code"] == "invalid", res[cid]
                assert "imp_tracker_url" in res["bad"]["error"]["message"]
                assert "click_tracker_url" in res["typo"]["error"]["message"]
            n = await conn.fetchval("SELECT count(*) FROM creatives WHERE campaign_id = $1", t.ids["camp"])
            assert n == 3
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_batch_without_new_fields_behaves_as_before(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            async with _client(t.user()) as c:
                r = await c.post("/creatives/batch", json=_batch(t, [_item("a")]))
                assert r.status_code == 200, r.text
                cr = r.json()[0]["creative"]
                assert not cr.get("imp_tracker_url") and not cr.get("click_tracker_url")
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_batch_limit_30(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            async with _client(t.user()) as c:
                r = await c.post("/creatives/batch", json=_batch(t, [_item(str(i)) for i in range(31)]))
                assert r.status_code == 422
                r = await c.post("/creatives/batch", json=_batch(t, [_item(str(i)) for i in range(30)]))
                assert r.status_code == 200, r.text
                assert all(x["status"] == "created" for x in r.json())
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_batch_is_idempotent_by_asset_url(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            async with _client(t.user()) as c:
                it = _item("a")
                r1 = await c.post("/creatives/batch", json=_batch(t, [it]))
                r2 = await c.post("/creatives/batch", json=_batch(t, [it]))
                a, b = r1.json()[0], r2.json()[0]
                assert a["status"] == b["status"] == "created"
                assert a["duplicate"] is False and b["duplicate"] is True
                assert a["creative"]["id"] == b["creative"]["id"]
            n = await conn.fetchval("SELECT count(*) FROM creatives WHERE campaign_id = $1", t.ids["camp"])
            assert n == 1
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_batch_foreign_campaign_same_as_single_create(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        alice = await _Tenant().setup(conn, role="manager")
        bob = await _Tenant().setup(conn, role="manager")
        try:
            async with _client(alice.user()) as c:
                single = await c.post("/creatives", json=_banner(bob, line_item_id=None))
                batch = await c.post("/creatives/batch", json={**_batch(bob, [_item("x")]), "line_item_id": None})
                assert single.status_code in (403, 404)
                assert batch.status_code == single.status_code
            n = await conn.fetchval("SELECT count(*) FROM creatives WHERE campaign_id = $1", bob.ids["camp"])
            assert n == 0
        finally:
            await alice.teardown(conn)
            await bob.teardown(conn)
            await conn.close()

    _run(scenario())


def test_batch_foreign_advertiser_skips_ord(env, monkeypatch):
    calls = []

    async def _no_ord(*a, **k):
        calls.append(a)
        raise AssertionError("ОРД не должен вызываться для рекламодателя foreign")

    monkeypatch.setattr(crs, "register_creative", _no_ord)

    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn, geo=("KZ",), ad_market="foreign")
        try:
            async with _client(t.user()) as c:
                r = await c.post("/creatives/batch", json=_batch(t, [_item("a"), _item("b", 728, 90)], kktu_code=None))
                assert r.status_code == 200, r.text
                for x in r.json():
                    assert x["status"] == "created"
                    assert x["creative"]["ord"]["status"] == "not_required"
            assert calls == []
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_batch_ru_admin_reports_ord_status_per_item(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn, geo=("RU",))
        try:
            async with _client(t.user()) as c:
                r = await c.post("/creatives/batch", json=_batch(t, [_item("a"), _item("b", 320, 50)]))
                assert r.status_code == 200, r.text
                for x in r.json():
                    assert x["status"] == "created"
                    assert x["creative"]["status"] == "approved"
                    assert x["creative"]["ord"]["status"] in {"ok", "awaiting_contract", "failed"}
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


# ------------------------------------------------------------- GET /creatives (превью)


def test_list_creatives_has_counterparty_fields(env):
    """PR-5: список отдаёт реквизиты контрагента для плашки «Реклама ⓘ» одним JOIN;
    креатив без контрагента — поля null, а не ошибка."""
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            cp = await conn.fetchval(
                """INSERT INTO counterparties (user_id, name, inn, ogrn, address)
                   VALUES ($1, 'ООО Плашка', '7701234567', '1027700000000', 'Москва, ул. Тестовая, 1') RETURNING id""",
                uuid.UUID(t.ids["user"]),
            )
            async with _client(t.user()) as c:
                with_cp = await c.post("/creatives", json=_banner(t, name="with-cp", counterparty_id=str(cp)))
                assert with_cp.status_code == 201, with_cp.text
                no_cp = await c.post("/creatives", json=_banner(t, name="no-cp"))
                assert no_cp.status_code == 201, no_cp.text
                r = await c.get("/creatives")
                assert r.status_code == 200
                rows = {x["name"]: x for x in r.json()}
                a = rows["with-cp"]
                assert a["counterparty_name"] == "ООО Плашка"
                assert a["counterparty_inn"] == "7701234567"
                assert a["counterparty_ogrn"] == "1027700000000"
                assert a["counterparty_address"] == "Москва, ул. Тестовая, 1"
                b = rows["no-cp"]
                for k in ("counterparty_name", "counterparty_inn", "counterparty_ogrn", "counterparty_address"):
                    assert k in b and b[k] is None
                r2 = await c.get("/creatives", params={"campaign_id": t.ids["camp"]})
                assert r2.status_code == 200 and len(r2.json()) == 2
        finally:
            # Сначала teardown (удаляет креативы), иначе FK creatives_counterparty_id_fkey не даст удалить контрагента.
            await t.teardown(conn)
            await conn.execute("DELETE FROM counterparties WHERE user_id = $1", uuid.UUID(t.ids["user"]))
            await conn.close()

    _run(scenario())
