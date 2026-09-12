"""Scope отчётов и статистики (audit S-02/S-03, план B12b).

Спецификация — docs/audit/scope_spec.md, раздел «Отчёты». Проверяется, что
/stats/* и /scoring/report используют тот же scope, что и CRUD
(rbac.report_scope_for), а не «только свой кабинет»:

* платформенные роли видят статистику всех владельцев (owner_ids = NULL);
* agency — свою + клиентов; manager — только свою;
* viewer с границей advertiser_id не видит других рекламодателей владельца ни в
  summary (включая счётчики), ни в timeseries/breakdown/domains/domain-quality,
  ни в POST /stats/report — даже если чужие campaign_ids переданы в body;
* /scoring/report для admin/agency строится в кабинете владельца кампании.

Требует DATABASE_URL; сущности создаются и удаляются за собой (users CASCADE).
"""
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg
import httpx
from fastapi import FastAPI

import db
import routers.scoring as scor
import routers.stats as statr
from auth import current_user
from rbac import Scope
from tests.test_scope_matrix_db import _delete_user, _plain_user, _Tenant

HOUR = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)


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
    app.include_router(statr.router)
    app.include_router(scor.router)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _add_stats(conn, camp: str, *, spend: float, domain: str) -> None:
    """Час статистики + домен для кампании: 10 показов, 1 клик, spend."""
    await conn.execute(
        """INSERT INTO stats_hourly (ts_hour, campaign_id, bids, wins, impressions, clicks, spend, billable_imps)
           VALUES ($1,$2,20,10,10,1,$3,10)""",
        HOUR, camp, spend,
    )
    await conn.execute(
        """INSERT INTO domain_stats_hourly (ts_hour, campaign_id, domain, wins, impressions, clicks, spend)
           VALUES ($1,$2,$3,10,10,1,$4)""",
        HOUR, camp, domain, spend,
    )


async def _second_advertiser(conn, t: _Tenant, tag: str) -> tuple[str, str]:
    """Второй рекламодатель того же владельца со своей кампанией."""
    adv = await conn.fetchval(
        "INSERT INTO advertisers (user_id, name, status, balance) VALUES ($1,$2,'active',1000) RETURNING id",
        t.ids["user"], f"adv2-{tag}",
    )
    camp = await conn.fetchval(
        """INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget)
           VALUES ($1,$2,$3,'active',100,0) RETURNING id""",
        adv, t.ids["user"], f"camp2-{tag}",
    )
    return str(adv), str(camp)


async def _guest(conn, owner: _Tenant, advertiser_id: str | None) -> dict:
    g = await _plain_user(conn, "viewer")
    await conn.execute(
        """INSERT INTO guest_access (user_id, owner_id, advertiser_id, markup_mult_snapshot)
           VALUES ($1,$2,$3,1.5)""",
        g["id"], owner.ids["user"], advertiser_id,
    )
    return g


# ---------------------------------------------------------------------------
# Scope.owner_ids_param: NULL = все, пустой список = никого.
# ---------------------------------------------------------------------------
def test_owner_ids_param_null_means_all_and_empty_means_none(env):
    assert Scope(all=True, user_ids=()).owner_ids_param is None
    assert Scope(all=False, user_ids=("a", "b")).owner_ids_param == ["a", "b"]
    assert Scope(all=False, user_ids=()).owner_ids_param == []
    assert Scope.owner_in("c.user_id") == "($1::uuid[] IS NULL OR c.user_id = ANY($1::uuid[]))"

    async def run():
        conn = await _conn()
        alice = await _Tenant("alice").setup(conn)
        try:
            await _add_stats(conn, alice.ids["camp"], spend=10, domain="a.example")
            sql = f"SELECT count(*) FROM campaigns c WHERE {Scope.owner_in('c.user_id')} AND c.id = $2"
            assert await conn.fetchval(sql, None, alice.ids["camp"]) == 1
            assert await conn.fetchval(sql, [alice.ids["user"]], alice.ids["camp"]) == 1
            assert await conn.fetchval(sql, [], alice.ids["camp"]) == 0
            assert await conn.fetchval(sql, [str(uuid.uuid4())], alice.ids["camp"]) == 0
        finally:
            await alice.teardown(conn)
            await conn.close()

    _run(run())


# ---------------------------------------------------------------------------
# Роли: платформенные видят всех, agency — клиентов, manager — только себя.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["superadmin", "admin", "analyst", "finance"])
def test_platform_role_stats_cover_all_owners(env, role):
    async def run():
        conn = await _conn()
        alice = await _Tenant("alice").setup(conn)
        bob = await _Tenant("bob").setup(conn)
        me = await _plain_user(conn, role)
        try:
            await _add_stats(conn, alice.ids["camp"], spend=10, domain="alice.example")
            await _add_stats(conn, bob.ids["camp"], spend=20, domain="bob.example")
            async with _app_as(me) as c:
                r = await c.get("/stats/summary", params={"hours": 48})
                assert r.status_code == 200, r.text
                s = r.json()
                assert s["impressions"] >= 20 and s["total_campaigns"] >= 2
                assert s["spend"] >= 30  # платформенные роли — без наценки

                r = await c.get("/stats/breakdown", params={"hours": 48, "dimension": "campaign"})
                ids = {row["id"] for row in r.json()}
                assert {alice.ids["camp"], bob.ids["camp"]} <= ids

                r = await c.get("/stats/domains", params={"hours": 48})
                assert {"alice.example", "bob.example"} <= {row["domain"] for row in r.json()}

                r = await c.post("/stats/report", json={
                    "date_from": HOUR.date().isoformat(), "date_to": datetime.now(UTC).date().isoformat(),
                    "groupings": ["campaign"], "campaign_ids": [alice.ids["camp"], bob.ids["camp"]],
                })
                assert r.status_code == 200, r.text
                assert {row["campaign_id"] for row in r.json()["rows"]} == {alice.ids["camp"], bob.ids["camp"]}
        finally:
            await _delete_user(conn, me["id"])
            await bob.teardown(conn)
            await alice.teardown(conn)
            await conn.close()

    _run(run())


def test_agency_sees_clients_manager_only_self(env):
    async def run():
        conn = await _conn()
        agency = await _Tenant("agency", role="agency").setup(conn)
        client = await _Tenant("client", role="client", parent=agency.ids["user"]).setup(conn)
        stranger = await _Tenant("stranger").setup(conn)
        try:
            for t, sp, dom in ((agency, 1, "ag.example"), (client, 2, "cl.example"), (stranger, 4, "st.example")):
                await _add_stats(conn, t.ids["camp"], spend=sp, domain=dom)
            async with _app_as(agency.as_user()) as c:
                r = await c.get("/stats/breakdown", params={"hours": 48})
                ids = {row["id"] for row in r.json()}
                assert {agency.ids["camp"], client.ids["camp"]} <= ids and stranger.ids["camp"] not in ids
                r = await c.get("/stats/domains", params={"hours": 48})
                doms = {row["domain"] for row in r.json()}
                assert {"ag.example", "cl.example"} <= doms and "st.example" not in doms
            async with _app_as(stranger.as_user()) as c:
                r = await c.get("/stats/breakdown", params={"hours": 48})
                assert {row["id"] for row in r.json()} == {stranger.ids["camp"]}
                r = await c.get("/stats/summary", params={"hours": 48})
                assert r.json()["impressions"] == 10 and r.json()["total_campaigns"] == 1
        finally:
            await stranger.teardown(conn)
            await client.teardown(conn)
            await agency.teardown(conn)
            await conn.close()

    _run(run())


# ---------------------------------------------------------------------------
# Viewer: граница одного рекламодателя во всех ручках.
# ---------------------------------------------------------------------------
def test_viewer_advertiser_boundary_on_every_stats_endpoint(env):
    async def run():
        conn = await _conn()
        alice = await _Tenant("alice").setup(conn)
        try:
            _adv2, camp2 = await _second_advertiser(conn, alice, "alice")
            await conn.execute("UPDATE campaigns SET status = 'active' WHERE id = $1", alice.ids["camp"])
            await _add_stats(conn, alice.ids["camp"], spend=10, domain="mine.example")
            await _add_stats(conn, camp2, spend=1000, domain="other.example")
            await conn.execute(
                "INSERT INTO domain_quality (domain, score) VALUES ('mine.example', 0.9), ('other.example', 0.1) "
                "ON CONFLICT (domain) DO NOTHING"
            )
            guest = await _guest(conn, alice, alice.ids["adv"])
            period = {"date_from": HOUR.date().isoformat(), "date_to": datetime.now(UTC).date().isoformat()}
            async with _app_as(guest) as c:
                s = (await c.get("/stats/summary", params={"hours": 48})).json()
                assert s["impressions"] == 10 and s["spend"] == pytest.approx(15.0)  # net 10 × snapshot 1.5
                assert s["total_campaigns"] == 1 and s["active_campaigns"] == 1
                assert s["total_creatives"] == 1 and s["active_partners"] == 0

                ts = (await c.get("/stats/timeseries", params={"hours": 48})).json()
                assert sum(p["impressions"] for p in ts) == 10

                for dim in ("campaign", "partner", "creative"):
                    rows = (await c.get("/stats/breakdown", params={"hours": 48, "dimension": dim})).json()
                    assert {row["id"] for row in rows} == {alice.ids["camp"]}, dim

                doms = (await c.get("/stats/domains", params={"hours": 48})).json()
                assert {row["domain"] for row in doms} == {"mine.example"}

                dq = (await c.get("/stats/domain-quality")).json()["domains"]
                assert "other.example" not in {row["domain"] for row in dq}

                r = await c.post("/stats/report", json={**period, "groupings": ["campaign"]})
                assert {row["campaign_id"] for row in r.json()["rows"]} == {alice.ids["camp"]}
                # чужой рекламодатель того же владельца в body → как будто его нет
                r = await c.post("/stats/report", json={**period, "groupings": ["campaign"], "campaign_ids": [camp2]})
                assert r.status_code == 200 and r.json()["rows"] == [] and r.json()["totals"] is None
                r = await c.post("/stats/report", json={**period, "groupings": ["campaign"],
                                                        "campaign_ids": [camp2, alice.ids["camp"]]})
                assert {row["campaign_id"] for row in r.json()["rows"]} == {alice.ids["camp"]}
                assert r.json()["totals"]["spend"] == pytest.approx(15.0)

            # гость без границы видит обоих рекламодателей владельца
            guest_all = await _guest(conn, alice, None)
            async with _app_as(guest_all) as c:
                s = (await c.get("/stats/summary", params={"hours": 48})).json()
                assert s["impressions"] == 20 and s["total_campaigns"] == 2
                doms = (await c.get("/stats/domains", params={"hours": 48})).json()
                assert {row["domain"] for row in doms} == {"mine.example", "other.example"}
        finally:
            await conn.execute("DELETE FROM domain_quality WHERE domain IN ('mine.example','other.example')")
            await alice.teardown(conn)
            await conn.close()

    _run(run())


# ---------------------------------------------------------------------------
# /scoring/report: владелец кампании, а не запрашивающий.
# ---------------------------------------------------------------------------
def test_scoring_report_uses_campaign_owner_for_admin_and_agency(env):
    async def run():
        conn = await _conn()
        agency = await _Tenant("agency", role="agency").setup(conn)
        client = await _Tenant("client", role="client", parent=agency.ids["user"]).setup(conn)
        stranger = await _Tenant("stranger").setup(conn)
        admin = await _plain_user(conn, "admin")
        try:
            await conn.execute(
                "UPDATE campaigns SET scoring_profile_id = $1 WHERE id = $2", client.ids["profile"], client.ids["camp"]
            )
            for who in (admin, agency.as_user()):
                async with _app_as(who) as c:
                    r = await c.get("/scoring/report", params={"campaign_id": client.ids["camp"], "refresh": "true"})
                    assert r.status_code == 200, (who["role"], r.text)
                    # профиль разрешён в кабинете владельца кампании
                    assert r.json()["profile"]["id"] == client.ids["profile"]
                    r = await c.get("/scoring/report", params={"group_id": client.ids["grp"], "refresh": "true"})
                    assert r.status_code == 200, (who["role"], r.text)
            async with _app_as(stranger.as_user()) as c:
                r = await c.get("/scoring/report", params={"campaign_id": client.ids["camp"], "refresh": "true"})
                assert r.status_code == 404
                r = await c.get("/scoring/report", params={"group_id": client.ids["grp"], "refresh": "true"})
                assert r.status_code == 404
        finally:
            await _delete_user(conn, admin["id"])
            await stranger.teardown(conn)
            await client.teardown(conn)
            await agency.teardown(conn)
            await conn.close()

    _run(run())
