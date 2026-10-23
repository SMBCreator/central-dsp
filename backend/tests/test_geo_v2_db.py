"""GEO v2 через БД (требует DATABASE_URL): фид для Go, API справочника,
алиасы SSP. Места — из общих fixtures (bidder-go/internal/geo/testdata)."""

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
import geo_v2  # noqa: E402
import routers.bidder_feed as feed  # noqa: E402
import routers.geo as geo_router  # noqa: E402
import routers.supply_partners as sp_router  # noqa: E402
from auth import current_user  # noqa: E402
from rbac import require_admin  # noqa: E402

FEED_TOKEN = "geo-v2-feed-token"
FIXTURES = json.loads(
    (Path(__file__).resolve().parents[2] / "bidder-go" / "internal" / "geo" / "testdata" / "fixtures.json")
    .read_text(encoding="utf-8")
)
NAMES = {  # name_ru / name_en для API-ответов
    2017370: ("Россия", "Russia"), 1522867: ("Казахстан", "Kazakhstan"),
    524894: ("Москва", "Moscow"), 524925: ("Московская область", "Moscow Oblast"),
    542415: ("Краснодарский край", "Krasnodar Krai"), 484048: ("Республика Татарстан", "Tatarstan Republic"),
    524304: ("Мурманская область", "Murmansk Oblast"), 536199: ("Ленинградская область", "Leningrad Oblast"),
    1526395: ("Алматы", "Almaty"), 524901: ("Москва", "Moscow"), 491422: ("Сочи", "Sochi"),
    542420: ("Краснодар", "Krasnodar"), 551487: ("Казань", "Kazan"), 548391: ("Кировск", "Kirovsk"),
    548392: ("Кировск", "Kirovsk"), 1526384: ("Алматы", "Almaty"),
}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("RTB_API_TOKEN", FEED_TOKEN)
    monkeypatch.setenv("GEO_V2_ENABLED", "1")
    db._pool = None
    geo_v2._cache.update(index=None, version=None, checked=0.0)
    yield
    db._pool = None


async def _seed(conn) -> dict:
    """Справочник из fixtures (если в БД ещё нет активной версии) + пользователь и SSP."""
    if not await conn.fetchval("SELECT 1 FROM geo_dataset_versions WHERE is_active"):
        await conn.execute(
            "INSERT INTO geo_dataset_versions (version, source, license, is_active) VALUES ('fixtures', 'test', 'test', true)"
        )
        for p in sorted(FIXTURES["places"], key=lambda x: x["t"] != "country"):
            ru, en = NAMES[p["id"]]
            await conn.execute(
                """INSERT INTO geo_places (id, country_iso, region_code, name_ru, name_en, name_ru_norm, name_en_norm,
                                           aliases, type, lat, lon, population, parent_id, dataset_version)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,'fixtures') ON CONFLICT (id) DO NOTHING""",
                p["id"], p["cc"], p.get("rc"), ru, en, p["n"][0], p["n"][1], p["n"], p["t"],
                p.get("lat"), p.get("lon"), p.get("pop", 0), p["p"] or None,
            )
    uid = await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','geo-v2','admin') RETURNING id",
        f"geo-v2-{uuid.uuid4()}@test.local",
    )
    partner = await conn.fetchval(
        "INSERT INTO supply_partners (user_id, name, endpoint_key) VALUES ($1,'ssp',$2) RETURNING id",
        uid, f"geo-{uuid.uuid4().hex[:10]}",
    )
    return {"user": {"id": uid, "role": "admin"}, "partner": str(partner)}


def _app(user: dict) -> FastAPI:
    app = FastAPI()
    for r in (feed.router, geo_router.router, sp_router.router):
        app.include_router(r)
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[require_admin] = lambda: user
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_api_feed_and_aliases(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            s = await _seed(conn)
        finally:
            await conn.close()
        auth = {"Authorization": f"Bearer {FEED_TOKEN}"}
        async with _client(_app(s["user"])) as c:
            cfg = (await c.get("/geo/config")).json()
            assert cfg["v2_enabled"] is True and cfg["max_places"] == 500 and cfg["dataset_version"]

            found = (await c.get("/geo/places/search", params={"q": "соч", "country": "RU"})).json()
            sochi = next(i for i in found["items"] if i["id"] == 491422)
            assert sochi["path"].startswith("Краснодарский край")
            by_alias = (await c.get("/geo/places/search", params={"q": "msk"})).json()["items"]
            assert {i["id"] for i in by_alias} >= {524894, 524901}
            assert (await c.get("/geo/places/search", params={"q": "x", "type": "planet"})).status_code == 422

            kids = (await c.get(f"/geo/places/542415/children", params={"type": "city,town"})).json()["items"]
            assert [k["id"] for k in kids][:2] == [542420, 491422]  # по населению

            labels = (await c.get("/geo/places", params={"ids": "491422,524901"})).json()
            assert {x["id"] for x in labels} == {491422, 524901}

            res = (await c.post("/geo/places/resolve", json={"lines": ["Сочи", "Москва", "Атлантида", " "]})).json()
            assert [r["status"] for r in res] == ["found", "ambiguous", "not_found"]
            assert res[1]["candidates"][0]["type"] == "region"

            r = await c.get("/rtb-feed/campaigns", headers=auth)
            v1 = r.json()["geo_version"]
            assert v1 and v1.startswith(cfg["dataset_version"] + ":")

            body = {"kind": "city", "ssp_value": "Moscow Center", "place_id": 524901}
            a = (await c.put(f"/supply-partners/{s['partner']}/geo-aliases", json=body)).json()
            again = (await c.put(f"/supply-partners/{s['partner']}/geo-aliases", json=body)).json()
            assert a["id"] == again["id"]  # идемпотентно
            bad = await c.put(f"/supply-partners/{s['partner']}/geo-aliases",
                              json={"kind": "region", "ssp_value": "x", "place_id": 524901})
            assert bad.status_code == 422  # город нельзя привязать как регион
            foreign = await c.get(f"/supply-partners/{uuid.uuid4()}/geo-aliases")
            assert foreign.status_code == 404

            v2 = (await c.get("/rtb-feed/campaigns", headers=auth)).json()["geo_version"]
            assert v2 != v1  # алиас меняет версию -> Go перезагрузит справочник

            g = (await c.get("/rtb-feed/geo", headers=auth)).json()
            assert g["version"] == v2
            assert any(p["id"] == 524894 and p["rc"] == "RU-MOW" for p in g["places"])
            assert {"partner_id": s["partner"], "kind": "city", "value_norm": "moscow center", "place_id": 524901} in g["aliases"]

            idx = await geo_v2.get_index(await db.get_pool())
            got = idx.resolve(s["partner"], "RUS", "", "moscow center")
            assert got.chain[:1] == (524901,)

            assert (await c.delete(f"/supply-partners/{s['partner']}/geo-aliases/{a['id']}")).status_code == 204
            assert (await c.delete(f"/supply-partners/{s['partner']}/geo-aliases/{a['id']}")).status_code == 404

    asyncio.run(scenario())


def test_geo_feed_is_off_without_flag(env, monkeypatch):
    monkeypatch.delenv("GEO_V2_ENABLED")

    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            s = await _seed(conn)
        finally:
            await conn.close()
        auth = {"Authorization": f"Bearer {FEED_TOKEN}"}
        async with _client(_app(s["user"])) as c:
            assert (await c.get("/rtb-feed/campaigns", headers=auth)).json()["geo_version"] is None
            assert (await c.get("/rtb-feed/geo", headers=auth)).status_code == 404
            assert (await c.get("/geo/config")).json()["v2_enabled"] is False

    asyncio.run(scenario())
