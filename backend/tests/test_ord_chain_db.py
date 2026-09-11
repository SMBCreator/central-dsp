"""Цепочка ОРД на кампании — сквозной поток (B13d, ord_spec §7 D2; требует DATABASE_URL).

Кампания → «Данные ОРД» (режим, рекламодатель, договоры) → подтверждение →
креативы наследуют рекламодателя и регистрируются на договор цепочки;
без подтверждения запуск блокируется (ord_chain), после — цепочка заморожена.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import httpx
from fastapi import FastAPI

import db
import routers.campaigns as camps
import routers.creatives as crs
import routers.moderation as mod
import routers.ord_router as ordr
from auth import current_user
from tests.test_ord_flow_db import FEED_TOKEN, _admin, _conn, _Env

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "050_campaign_ord_chain.sql"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("RTB_API_TOKEN", FEED_TOKEN)
    monkeypatch.delenv("ORD_OZON_API_KEY", raising=False)
    monkeypatch.setenv("LAUNCH_GATE", "on")
    db._pool = None
    yield
    db._pool = None


def _app_as(user_row: dict) -> httpx.AsyncClient:
    app = FastAPI()
    for r in (camps.router, crs.router, mod.router, ordr.router):
        app.include_router(r)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _chain_env(conn) -> tuple[_Env, dict[str, str]]:
    """Кампания в draft, ОРС отмечена is_self, агентство + два зарегистрированных договора."""
    e = await _Env().setup(conn, geo=["RU"])
    await conn.execute("UPDATE campaigns SET status = 'draft' WHERE id = $1", e.ids["camp"])
    # is_self — одна на платформу: временно снимаем чужую, вернём в _restore_self
    e.ids["prev_self"] = str(await conn.fetchval("SELECT id FROM counterparties WHERE is_self") or "")
    await conn.execute("UPDATE counterparties SET is_self = false WHERE is_self")
    await conn.execute("UPDATE counterparties SET is_self = true WHERE id = $1", e.ids["ors"])
    agency = await conn.fetchval(
        """INSERT INTO counterparties (user_id, name, inn, legal_type, role, ord_status, ord_external_id)
           VALUES ($1,'Агентство','7700000003','ul','agency','registered','CP-3') RETURNING id""",
        e.ids["user"],
    )
    initial = await conn.fetchval(
        """INSERT INTO ord_contracts (user_id, client_id, contractor_id, number, signed_at, ord_status, ord_external_id)
           VALUES ($1,$2,$3,'И-1','2026-01-01','registered','CT-I1') RETURNING id""",
        e.ids["user"], e.ids["cp"], agency,
    )
    executive = await conn.fetchval(
        """INSERT INTO ord_contracts (user_id, client_id, contractor_id, number, signed_at, ord_status, ord_external_id)
           VALUES ($1,$2,$3,'Р-1','2026-01-01','registered','CT-E1') RETURNING id""",
        e.ids["user"], agency, e.ids["ors"],
    )
    return e, {"agency": str(agency), "initial": str(initial), "executive": str(executive)}


async def _teardown(e: _Env, conn):
    await e.teardown(conn)
    if e.ids.get("prev_self"):
        await conn.execute("UPDATE counterparties SET is_self = true WHERE id = $1", e.ids["prev_self"])


def test_campaign_chain_confirm_gate_lock_and_creative_inheritance(env):
    async def scenario():
        conn = await _conn()
        e, ids = await _chain_env(conn)
        try:
            client = _app_as(_admin(e))
            camp = e.ids["camp"]

            # 1. Цепочка не задана: сводка говорит «не выбран режим», запуск заблокирован ord_chain.
            r = await client.get(f"/campaigns/{camp}/ord-chain")
            assert r.status_code == 200, r.text
            assert r.json()["problems"] == ["mode_missing"] and r.json()["confirmed"] is False
            r = await client.patch(f"/campaigns/{camp}", json={"status": "active"})
            assert r.status_code == 409, r.text
            assert "ord_chain" in {b["code"] for b in r.json()["detail"]["blockers"]}
            r = await client.post(f"/campaigns/{camp}/ord-chain/confirm")
            assert r.status_code == 422 and r.json()["detail"]["code"] == "ord_chain_required"

            # 1a. Превью до создания кампании (мастер) — тот же валидатор, без записи.
            r = await client.post("/campaigns/ord-chain/preview", json={
                "advertiser_id": e.ids["adv"], "ord_mode": "own_client", "counterparty_id": e.ids["cp"],
                "initial_contract_id": ids["initial"], "executive_contract_id": ids["executive"],
            })
            assert r.status_code == 200, r.text
            assert r.json()["ready"] is True and r.json()["campaign_id"] is None
            r = await client.post("/campaigns/ord-chain/preview", json={
                "advertiser_id": e.ids["adv"], "ord_mode": "own_client", "counterparty_id": e.ids["cp"],
            })
            assert r.json()["problems"] == ["initial_missing", "executive_missing"]

            # 2. Заполняем цепочку через PATCH кампании (обычные поля).
            r = await client.patch(f"/campaigns/{camp}", json={
                "ord_mode": "own_client", "counterparty_id": e.ids["cp"],
                "initial_contract_id": ids["initial"], "executive_contract_id": ids["executive"],
            })
            assert r.status_code == 200, r.text
            assert r.json()["ord_mode"] == "own_client" and r.json()["ord_confirmed_at"] is None
            chain = (await client.get(f"/campaigns/{camp}/ord-chain")).json()
            assert chain["ready"] is True and chain["problems"] == []
            assert chain["registration_contract_id"] == ids["initial"]
            assert chain["self_org"]["is_self"] is True
            assert chain["executive_contract"]["contractor"]["id"] == e.ids["ors"]

            # 3. Подтверждение — идемпотентно, снимает блокер ord_chain.
            r = await client.post(f"/campaigns/{camp}/ord-chain/confirm")
            assert r.status_code == 200 and r.json()["confirmed"] is True
            first = r.json()["confirmed_at"]
            r = await client.post(f"/campaigns/{camp}/ord-chain/confirm")
            assert r.json()["confirmed_at"] == first
            r = await client.patch(f"/campaigns/{camp}", json={"status": "active"})
            assert r.status_code == 409  # остаются другие блокеры (креативов нет)
            assert "ord_chain" not in {b["code"] for b in r.json()["detail"]["blockers"]}

            # 4. Заморозка: цепочку менять нельзя, остальное — можно.
            r = await client.patch(f"/campaigns/{camp}", json={"counterparty_id": ids["agency"]})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "ord_locked"
            r = await client.patch(f"/campaigns/{camp}", json={"name": "renamed", "counterparty_id": e.ids["cp"]})
            assert r.status_code == 200, r.text

            # 5. Креатив без контрагента наследует рекламодателя кампании и
            #    при approve регистрируется на изначальный договор цепочки.
            r = await client.post("/creatives", json={
                "campaign_id": camp, "name": "b", "type": "banner", "width": 300, "height": 250,
                "asset_url": "http://a/i.png", "click_url": "http://x", "kktu_code": "30.10",
                "counterparty_id": ids["agency"], "contract_id": ids["executive"],
            })
            assert r.status_code == 201, r.text
            cr = r.json()
            assert cr["counterparty_id"] == e.ids["cp"], "контрагент креатива = рекламодатель кампании"
            row = await conn.fetchrow("SELECT erid, contract_id, counterparty_id FROM creatives WHERE id = $1", cr["id"])
            # админ → сразу approved + авторегистрация в mock-ОРД
            assert row["erid"] and row["erid"].startswith("MOCK-")
            assert str(row["contract_id"]) == ids["initial"]
            assert str(row["counterparty_id"]) == e.ids["cp"]

            # 6. Миграция идемпотентна.
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))
        finally:
            await _teardown(e, conn)
            await conn.close()

    asyncio.run(scenario())


def test_unconfirmed_chain_blocks_creative_registration_with_clear_reason(env):
    async def scenario():
        conn = await _conn()
        e, ids = await _chain_env(conn)
        try:
            await conn.execute(
                """UPDATE campaigns SET ord_mode='own_client', counterparty_id=$2,
                          initial_contract_id=$3, executive_contract_id=$4 WHERE id=$1""",
                e.ids["camp"], e.ids["cp"], ids["initial"], ids["executive"],
            )
            cr = await e.add_creative(conn, status="pending")
            client = _app_as(_admin(e))
            r = await client.post(f"/moderation/{cr}/approve")
            assert r.status_code == 200, r.text
            assert r.json()["ord"]["status"] != "ok"
            assert "не подтверждена" in r.json()["ord"]["error"]
            assert await conn.fetchval("SELECT erid FROM creatives WHERE id = $1", cr) is None
        finally:
            await _teardown(e, conn)
            await conn.close()

    asyncio.run(scenario())


def test_self_mode_registers_on_executive_contract(env):
    async def scenario():
        conn = await _conn()
        e, _ids = await _chain_env(conn)
        try:
            direct = await e.add_contract(conn, number="П-1")  # client = cp, contractor = ors
            client = _app_as(_admin(e))
            camp = e.ids["camp"]
            r = await client.patch(f"/campaigns/{camp}", json={
                "ord_mode": "self", "counterparty_id": e.ids["cp"], "executive_contract_id": direct,
            })
            assert r.status_code == 200, r.text
            chain = (await client.get(f"/campaigns/{camp}/ord-chain")).json()
            assert chain["ready"] is True, chain["problems"]
            assert chain["registration_contract_id"] == direct
            assert (await client.post(f"/campaigns/{camp}/ord-chain/confirm")).status_code == 200
            cr = await e.add_creative(conn, status="pending", counterparty=False)
            r = await client.post(f"/moderation/{cr}/approve")
            assert r.status_code == 200 and r.json()["ord"]["status"] == "ok", r.text
            assert str(await conn.fetchval("SELECT contract_id FROM creatives WHERE id = $1", cr)) == direct
        finally:
            await _teardown(e, conn)
            await conn.close()

    asyncio.run(scenario())
