"""Audit S-01: Origin/Referer-гейт для мутирующих запросов + SameSite cookie.

Unit-часть — чистая функция `csrf.verdict` и middleware на мини-приложении.
DB-часть — реальный роутер кампаний: PATCH с чужим Origin -> 403 и строка
в БД не изменилась; с правильным Origin -> 200 и изменилась.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import csrf  # noqa: E402
import startup_checks as sc  # noqa: E402
from csrf import OriginGuardMiddleware, allowed_origins, cookie_samesite, verdict  # noqa: E402

OUR = frozenset({"https://dsp.example.com"})
H_OUR = {"origin": "https://dsp.example.com"}
H_EVIL = {"origin": "https://evil.example"}


# --- verdict ------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_are_never_checked(method):
    assert verdict(method, "/campaigns/1", H_EVIL, allowed=OUR, production=True) is None


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_foreign_origin_is_rejected(method):
    assert verdict(method, "/campaigns/1", H_EVIL, allowed=OUR, production=True)


def test_own_origin_is_allowed():
    assert verdict("POST", "/campaigns", H_OUR, allowed=OUR, production=True) is None


def test_origin_match_is_case_insensitive_and_ignores_trailing_slash():
    assert verdict("POST", "/x", {"origin": "HTTPS://DSP.Example.com/"}, allowed=OUR, production=True) is None


def test_referer_used_when_origin_absent():
    ok = {"referer": "https://dsp.example.com/campaigns?x=1"}
    bad = {"referer": "https://evil.example/campaigns"}
    assert verdict("POST", "/x", ok, allowed=OUR, production=True) is None
    assert verdict("POST", "/x", bad, allowed=OUR, production=True)


def test_null_origin_is_rejected():
    assert verdict("POST", "/x", {"origin": "null"}, allowed=OUR, production=True)


def test_non_browser_client_without_origin_or_referer_passes():
    """curl/скрипты/тесты: cookie жертвы у них нет — CSRF неприменим."""
    assert verdict("POST", "/x", {}, allowed=OUR, production=True) is None


def test_same_origin_by_forwarded_host_without_public_origins():
    """За Caddy/Vercel без PUBLIC_ORIGINS: origin запроса восстанавливается из X-Forwarded-*."""
    h = {"origin": "https://dsp.example.com", "x-forwarded-host": "dsp.example.com", "x-forwarded-proto": "https"}
    assert verdict("POST", "/x", h, allowed=frozenset(), production=True) is None
    h_bad = dict(h, origin="https://evil.example")
    assert verdict("POST", "/x", h_bad, allowed=frozenset(), production=True)


def test_same_origin_by_plain_host_defaults_to_https_in_production():
    h = {"origin": "https://dsp.example.com", "host": "dsp.example.com"}
    assert verdict("POST", "/x", h, allowed=frozenset(), production=True) is None
    # http-origin на https-хосте — не same-origin
    assert verdict("POST", "/x", {"origin": "http://dsp.example.com", "host": "dsp.example.com"},
                   allowed=frozenset(), production=True)


def test_forwarded_host_cannot_be_spoofed_into_allowlist():
    """Явный PUBLIC_ORIGINS не расширяется заголовками: evil origin + evil forwarded host
    совпадают между собой, но self_origin — это наш публичный хост только когда его
    поставил наш proxy. Тест фиксирует, что при заданном allowlist чужой origin
    всё равно отклоняется, если X-Forwarded-Host указывает на наш хост."""
    h = {"origin": "https://evil.example", "x-forwarded-host": "dsp.example.com", "x-forwarded-proto": "https"}
    assert verdict("POST", "/x", h, allowed=OUR, production=True)


@pytest.mark.parametrize("path", ["/rtb/bid/abc", "/rtb-feed/candidates", "/pixel/collect", "/cron/reset-daily"])
def test_own_auth_endpoints_are_exempt(path):
    assert verdict("POST", path, H_EVIL, allowed=OUR, production=True) is None


@pytest.mark.parametrize("path", ["/pixels", "/pixels/1/goals", "/share/links", "/billing/deposit", "/auth/login"])
def test_cookie_endpoints_are_protected(path):
    assert verdict("POST", path, H_EVIL, allowed=OUR, production=True)


def test_allowed_origins_parsing():
    env = {"PUBLIC_ORIGINS": " https://a.example , HTTPS://B.example:8443/ ,, garbage , null "}
    assert allowed_origins(env) == frozenset({"https://a.example", "https://b.example:8443"})
    assert allowed_origins({}) == frozenset()


# --- middleware ----------------------------------------------------------------


def _mini_app(production: bool, env: dict) -> httpx.AsyncClient:
    app = FastAPI()
    hits: list[str] = []

    @app.post("/campaigns/{cid}")
    async def mutate(cid: str):
        hits.append(cid)
        return {"ok": True}

    @app.post("/rtb/bid/{key}")
    async def rtb(key: str):
        return {"bid": key}

    @app.get("/campaigns/{cid}")
    async def read(cid: str):
        return {"id": cid}

    app.add_middleware(OriginGuardMiddleware, production=production, env=env)
    app.state.hits = hits
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://dsp.example.com")


def test_middleware_rejects_cross_site_post_and_does_not_reach_handler():
    async def run():
        c = _mini_app(True, {"PUBLIC_ORIGINS": "https://dsp.example.com"})
        r = await c.post("/campaigns/1", headers=H_EVIL)
        assert r.status_code == 403
        assert "Origin" in r.json()["detail"]
        r = await c.post("/campaigns/1", headers=H_OUR)
        assert r.status_code == 200
        r = await c.get("/campaigns/1", headers=H_EVIL)
        assert r.status_code == 200
        r = await c.post("/rtb/bid/k", headers=H_EVIL)
        assert r.status_code == 200
        # non-browser (без Origin) — проходит
        r = await c.post("/campaigns/2")
        assert r.status_code == 200

    asyncio.run(run())


def test_middleware_only_logs_outside_production_without_public_origins():
    async def run():
        c = _mini_app(False, {})
        r = await c.post("/campaigns/1", headers=H_EVIL)
        assert r.status_code == 200

    asyncio.run(run())


def test_middleware_enforces_in_dev_when_public_origins_set():
    async def run():
        c = _mini_app(False, {"PUBLIC_ORIGINS": "http://localhost:3000"})
        assert (await c.post("/campaigns/1", headers=H_EVIL)).status_code == 403
        assert (await c.post("/campaigns/1", headers={"origin": "http://localhost:3000"})).status_code == 200

    asyncio.run(run())


def test_metric_counts_rejections():
    import obs_metrics

    obs_metrics.reset_for_tests()

    async def run():
        c = _mini_app(True, {"PUBLIC_ORIGINS": "https://dsp.example.com"})
        await c.post("/campaigns/1", headers=H_EVIL)
        await c.post("/campaigns/1", headers=H_EVIL)

    asyncio.run(run())
    assert obs_metrics.snapshot()["counters"]["csrf_rejected_total"] == 2


# --- cookie samesite / startup ------------------------------------------------


def test_cookie_samesite_defaults():
    assert cookie_samesite({"APP_ENV": "production"}) == "lax"
    assert cookie_samesite({"APP_ENV": "dev"}) == "none"
    assert cookie_samesite({}) == "none"
    assert cookie_samesite({"APP_ENV": "production", "AUTH_COOKIE_SAMESITE": "none"}) == "none"
    assert cookie_samesite({"APP_ENV": "dev", "AUTH_COOKIE_SAMESITE": "Strict"}) == "strict"
    assert cookie_samesite({"APP_ENV": "production", "AUTH_COOKIE_SAMESITE": "bogus"}) == "lax"


def test_startup_warns_about_missing_public_origins_and_samesite_none():
    base = {"APP_ENV": "production"}
    assert any("PUBLIC_ORIGINS" in w for w in sc.collect_warnings(base))
    assert not any("PUBLIC_ORIGINS" in w for w in sc.collect_warnings({**base, "PUBLIC_ORIGINS": "https://a"}))
    assert any("AUTH_COOKIE_SAMESITE" in w for w in sc.collect_warnings({**base, "AUTH_COOKIE_SAMESITE": "none"}))


def test_main_registers_origin_guard():
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "add_middleware(OriginGuardMiddleware)" in src


# --- DB: реальный роутер кампаний -------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark_db = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


@pytestmark_db
def test_patch_campaign_with_foreign_origin_is_rejected_and_db_untouched(monkeypatch):
    import asyncpg

    import db
    import routers.campaigns as campr
    from auth import current_user

    monkeypatch.setenv("PUBLIC_ORIGINS", "https://dsp.example.com")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LAUNCH_GATE", "off")
    db._pool = None

    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        uid = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','csrf','manager') RETURNING id",
            f"csrf-{uuid.uuid4()}@test.local",
        )
        try:
            adv = await conn.fetchval(
                "INSERT INTO advertisers (user_id, name, status, balance) VALUES ($1,'adv','active',100) RETURNING id", uid)
            camp = await conn.fetchval(
                """INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget)
                   VALUES ($1,$2,'before','paused',10,0) RETURNING id""", adv, uid)

            app = FastAPI()
            app.include_router(campr.router)
            app.add_middleware(OriginGuardMiddleware)

            async def _user():
                return {"id": str(uid), "email": "csrf@x", "name": "csrf", "role": "manager",
                        "status": "active", "permissions": {}}

            app.dependency_overrides[current_user] = _user
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://dsp.example.com")

            r = await c.patch(f"/campaigns/{camp}", json={"name": "pwned"}, headers=H_EVIL)
            assert r.status_code == 403, r.text
            assert await conn.fetchval("SELECT name FROM campaigns WHERE id = $1", camp) == "before"

            r = await c.patch(f"/campaigns/{camp}", json={"name": "legit"}, headers=H_OUR)
            assert r.status_code == 200, r.text
            assert await conn.fetchval("SELECT name FROM campaigns WHERE id = $1", camp) == "legit"
        finally:
            await conn.execute("DELETE FROM users WHERE id = $1", uid)
            await conn.close()
            db._pool = None

    asyncio.run(run())
