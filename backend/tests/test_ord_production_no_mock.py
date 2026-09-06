"""Audit L-01: mock-ОРД запрещён в production.

Исходная проблема: без ключа ОРД фабрика молча отдавала MockOrdAdapter,
креативы получали erid 'MOCK-…' и торговались в РФ как промаркированные.

Unit-часть — без БД. DB-часть (DATABASE_URL) — mock-erid из dev-БД не
проходит erid-гейт фида и launch-диагностику в production.
"""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import startup_checks  # noqa: E402
from ord import OrdNotConfigured, get_ord_adapter  # noqa: E402
from ord import service as ord_service  # noqa: E402
from ord.adapter import mock_allowed, mock_erid_accepted  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")


@pytest.fixture
def no_ord_key(monkeypatch):
    monkeypatch.delenv("ORD_OZON_API_KEY", raising=False)
    monkeypatch.delenv("ORD_FEED_GATE", raising=False)
    monkeypatch.delenv("LAUNCH_GATE", raising=False)
    monkeypatch.delenv("ORD_LAUNCH_GUARD", raising=False)
    monkeypatch.delenv("ORD_REQUIRED", raising=False)
    # get_ord_settings сперва идёт в БД — для unit-части её нет
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def production(monkeypatch, no_ord_key):
    monkeypatch.setenv("APP_ENV", "production")


@pytest.fixture
def development(monkeypatch, no_ord_key):
    monkeypatch.setenv("APP_ENV", "development")


# ------------------------------------------------------------------- factory --
def test_production_without_key_raises_not_configured(production):
    with pytest.raises(OrdNotConfigured) as ei:
        asyncio.run(get_ord_adapter())
    assert "ORD_OZON_API_KEY" in str(ei.value)
    assert not mock_allowed() and not mock_erid_accepted()


def test_production_has_no_override_flag(production, monkeypatch):
    """Никакого ORD_ALLOW_MOCK: единственный легальный путь — ключ или ORD_REQUIRED=0,
    и даже ORD_REQUIRED=0 не включает mock-erid."""
    monkeypatch.setenv("ORD_ALLOW_MOCK", "1")
    monkeypatch.setenv("ORD_REQUIRED", "0")
    with pytest.raises(OrdNotConfigured):
        asyncio.run(get_ord_adapter())


def test_dev_without_key_still_returns_mock(development):
    assert type(asyncio.run(get_ord_adapter())).__name__ == "MockOrdAdapter"
    assert mock_allowed() and mock_erid_accepted()


def test_production_with_key_returns_ozon(production, monkeypatch):
    monkeypatch.setenv("ORD_OZON_API_KEY", "k" * 32)
    assert type(asyncio.run(get_ord_adapter())).__name__ == "OzonOrdClient"


# ------------------------------------------------------------------ feed gate --
def test_feed_gate_cannot_be_disabled_in_production(production, monkeypatch):
    monkeypatch.setenv("ORD_FEED_GATE", "off")
    assert ord_service.feed_gate_enabled() is True
    sql = ord_service.feed_gate_sql()
    assert "MOCK-%" in sql and "ord_provider = 'mock'" in sql


def test_feed_gate_off_still_works_in_dev(development, monkeypatch):
    monkeypatch.setenv("ORD_FEED_GATE", "off")
    assert ord_service.feed_gate_sql() == ""
    monkeypatch.delenv("ORD_FEED_GATE")
    sql = ord_service.feed_gate_sql()
    assert sql and "MOCK-%" not in sql  # dev принимает mock-erid


def test_unmarked_predicate_matches_gate_semantics(production):
    prod_sql = ord_service.unmarked_creative_sql("c")
    assert "c.erid LIKE 'MOCK-%'" in prod_sql and "oc.creative_id = c.id" in prod_sql


# ------------------------------------------------------------ startup checks --
def test_startup_requires_ord_in_production(production):
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(startup_checks.check_ord_configured(production=True))
    assert "L-01" in str(ei.value)


def test_startup_allows_explicit_ord_required_zero(production, monkeypatch, caplog):
    monkeypatch.setenv("ORD_REQUIRED", "0")
    with caplog.at_level("WARNING"):
        asyncio.run(startup_checks.check_ord_configured(production=True))
    assert any("ORD_REQUIRED=0" in r.message for r in caplog.records)


def test_startup_passes_with_key(production, monkeypatch):
    monkeypatch.setenv("ORD_OZON_API_KEY", "k" * 32)
    asyncio.run(startup_checks.check_ord_configured(production=True))


def test_startup_skips_outside_production(development):
    asyncio.run(startup_checks.check_ord_configured(production=False))


def test_gate_off_flags_are_warned_in_production():
    env = {"APP_ENV": "production", "ORD_FEED_GATE": "off", "LAUNCH_GATE": "off", "ORD_REQUIRED": "0"}
    w = "\n".join(startup_checks.collect_warnings(env))
    assert "ORD_FEED_GATE=off" in w and "LAUNCH_GATE=off" in w and "ORD_REQUIRED=0" in w


# ------------------------------------------------------------------ DB part --
@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")
def test_existing_mock_erid_is_rejected_by_feed_and_diagnostics_in_production(monkeypatch):
    """Креатив, промаркированный mock-адаптером на dev, попадает в production-БД.
    Ожидание: фид его не отдаёт (РФ-гео), диагностика кампании считает его
    непромаркированным, а честный erid с провайдером ozon — проходит."""
    import asyncpg
    import httpx
    from fastapi import FastAPI

    import db
    import routers.bidder_feed as feed
    import routers.campaigns as camps
    from auth import current_user

    token = "feed-test-token"
    monkeypatch.setenv("RTB_API_TOKEN", token)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ORD_OZON_API_KEY", raising=False)
    monkeypatch.delenv("ORD_FEED_GATE", raising=False)
    db._pool = None

    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        uid = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','l01','admin') RETURNING id",
            f"l01-{uuid.uuid4()}@test.local",
        )
        try:
            adv = await conn.fetchval(
                "INSERT INTO advertisers (user_id, name, status, balance) VALUES ($1,'adv','active',1000) RETURNING id", uid
            )
            camp = await conn.fetchval(
                """INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget)
                   VALUES ($1,$2,'camp','active',100,0) RETURNING id""",
                adv, uid,
            )
            await conn.execute(
                """INSERT INTO line_items (campaign_id, user_id, name, bid_price, status, targeting)
                   VALUES ($1,$2,'li',1.5,'active',$3::jsonb)""",
                camp, uid, json.dumps({"geo_countries": ["RU"]}),
            )

            async def creative(erid, provider):
                cr = await conn.fetchval(
                    """INSERT INTO creatives (campaign_id, user_id, name, type, width, height, asset_url, click_url,
                                              status, erid, kktu_code)
                       VALUES ($1,$2,'b','banner',300,250,'http://a/i.png','http://x','approved',$3,'30.10') RETURNING id""",
                    camp, uid, erid,
                )
                if provider:
                    await conn.execute(
                        """INSERT INTO ord_creatives (creative_id, status, erid, ord_provider, registered_at)
                           VALUES ($1,'registered',$2,$3,now())""",
                        cr, erid, provider,
                    )
                return str(cr)

            mock_by_provider = await creative("2VtzqvABCDEF1", "mock")   # честный вид, но провайдер mock
            mock_by_prefix = await creative("MOCK-deadbeef0001", None)    # erid руками, строки ord_creatives нет
            real = await creative("2VtzqvREAL0001", "ozon")

            app = FastAPI()
            app.include_router(feed.router)
            app.include_router(camps.router)

            async def _fake_user():
                return {"id": str(uid), "email": "a@x", "name": "A", "role": "admin",
                        "status": "active", "permissions": {}}

            app.dependency_overrides[current_user] = _fake_user
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                r = await client.get("/rtb-feed/campaigns", headers={"Authorization": f"Bearer {token}"})
                assert r.status_code == 200, r.text
                in_feed = {c["creative_id"] for c in r.json()["candidates"]}
                assert real in in_feed
                assert mock_by_provider not in in_feed
                assert mock_by_prefix not in in_feed

                d = await client.get(f"/campaigns/{camp}/diagnostics")
                assert d.status_code == 200, d.text
                marking = next(c for c in d.json()["checks"] if c["code"] == "ord_marking")
                assert marking["ok"] is False
                assert "2 одобренных" in marking["detail"]
        finally:
            await conn.execute("DELETE FROM users WHERE id = $1", uid)
            await conn.close()
            db._pool = None

    asyncio.run(scenario())
