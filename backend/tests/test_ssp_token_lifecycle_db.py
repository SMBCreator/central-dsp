"""Жизненный цикл SSP-токена и валидация whitelist на реальном Postgres (audit S-06).

Фиксируем контракт роутера supply_partners после миграции 039:
  * partner без токена нельзя перевести в testing/active (422) и нельзя проверить;
  * ротация атомарна: старый хэш уезжает в token_prev_hash с окном 15 мин,
    фид отдаёт prev только пока окно открыто; Python process_bid_request
    принимает старый токен в окне и отвергает после;
  * revoke -> paused + token_revoked_at, prev-окно закрыто, hash пуст ->
    process_bid_request отвечает 403 (а не пускает без авторизации);
  * ip_whitelist с мусором -> 422 и в БД не попадает; валидный нормализуется.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg  # noqa: E402
import httpx  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402

import db  # noqa: E402
import ssp_check  # noqa: E402
import ssp_tokens  # noqa: E402
from auth import current_user  # noqa: E402
from routers import bidder_feed, supply_partners as sp_router  # noqa: E402


class _AuthPassed(Exception):
    """Маркер: process_bid_request прошёл auth и дошёл до QPS-гейта."""


@pytest.fixture
def env(monkeypatch):
    from routers import rtb

    db._pool = None
    # Reload фида Go в тесте не нужен и не должен ходить в сеть.
    async def _noop(client=None):
        return False
    monkeypatch.setattr(ssp_check, "request_feed_reload", _noop)
    monkeypatch.setenv("RTB_API_TOKEN", "feed-token-test")
    # Redis в тесте нет: QPS-гейт идёт сразу после auth — останавливаемся на нём.
    async def _qps(*_a, **_k):
        raise _AuthPassed
    monkeypatch.setattr(rtb, "check_qps", _qps)
    yield
    db._pool = None


def _client(user_row: dict) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(sp_router.router)
    app.include_router(bidder_feed.router)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _admin(conn) -> dict:
    uid = await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','adm','admin') RETURNING id",
        f"s06-{uuid.uuid4()}@test.local",
    )
    return {"id": uid, "email": "adm@x", "name": "adm", "role": "admin"}


async def _bid(endpoint_key: str, token: str | None) -> int:
    """Статус auth-фазы process_bid_request: 403/404 или 200 (auth пройден).
    RTB_CACHE_TTL=0 по умолчанию, партнёр читается из БД на каждый вызов."""
    from routers import rtb

    try:
        await rtb.process_bid_request(endpoint_key, {"id": "x", "imp": []}, ssp_token=token, client_ip=None)
    except HTTPException as exc:
        return exc.status_code
    except _AuthPassed:
        return 200
    return 200


def test_token_lifecycle_and_whitelist_validation(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        admin = await _admin(conn)
        feed_hdr = {"Authorization": "Bearer feed-token-test"}
        try:
            async with _client(admin) as c:
                # --- создание с мусорным whitelist -> 422, в БД ничего
                r = await c.post("/supply-partners", json={"name": "SSP X", "ip_whitelist": ["203.0.113.7", "garbage"]})
                assert r.status_code == 422, r.text
                assert "garbage" in r.json()["detail"]

                r = await c.post("/supply-partners", json={"name": "SSP X", "ip_whitelist": [" 10.1.2.3/16 ", "2001:db8::1"]})
                assert r.status_code == 201, r.text
                p = r.json()
                pid, ek = p["id"], p["endpoint_key"]
                assert p["ip_whitelist"] == ["10.1.0.0/16", "2001:db8::1"]
                assert p["has_auth_token"] is False and p["status"] == "draft"

                # --- без токена нельзя включить и нельзя проверить
                r = await c.patch(f"/supply-partners/{pid}", json={"status": "testing"})
                assert r.status_code == 422 and "токен" in r.json()["detail"].lower()
                r = await c.patch(f"/supply-partners/{pid}", json={"status": "active"})
                assert r.status_code == 422
                r = await c.post(f"/supply-partners/{pid}/check")
                assert r.status_code == 422
                # paused/draft без токена — можно (это не включение)
                r = await c.patch(f"/supply-partners/{pid}", json={"status": "paused"})
                assert r.status_code == 200 and r.json()["status"] == "paused"

                # --- PATCH whitelist с мусором -> 422, старое значение сохраняется
                r = await c.patch(f"/supply-partners/{pid}", json={"ip_whitelist": ["999.1.1.1"]})
                assert r.status_code == 422 and "999.1.1.1" in r.json()["detail"]
                row = await conn.fetchrow("SELECT ip_whitelist::text AS wl FROM supply_partners WHERE id = $1", pid)
                assert "10.1.0.0/16" in row["wl"]

                # --- первый выпуск токена: prev не заводится
                r = await c.post(f"/supply-partners/{pid}/token")
                assert r.status_code == 200
                tok1 = r.json()["token"]
                assert r.json()["header"] == ssp_tokens.HEADER_NAME
                assert r.json()["prev_token_valid_until"] is None
                row = await conn.fetchrow("SELECT * FROM supply_partners WHERE id = $1", pid)
                assert row["auth_token_hash"] == ssp_tokens.hash_token(tok1)
                assert row["token_prev_hash"] is None and row["token_revoked_at"] is None

                # теперь включить можно
                r = await c.patch(f"/supply-partners/{pid}", json={"status": "active"})
                assert r.status_code == 200 and r.json()["status"] == "active"

                assert await _bid(ek, tok1) == 200
                assert await _bid(ek, "wrong") == 403
                assert await _bid(ek, None) == 403

                # --- ротация: старый токен живёт в окне, новый работает сразу
                r = await c.post(f"/supply-partners/{pid}/token")
                tok2 = r.json()["token"]
                assert r.json()["prev_token_valid_until"] is not None
                row = await conn.fetchrow("SELECT * FROM supply_partners WHERE id = $1", pid)
                assert row["auth_token_hash"] == ssp_tokens.hash_token(tok2)
                assert row["token_prev_hash"] == ssp_tokens.hash_token(tok1)
                left = row["token_prev_valid_until"] - row["updated_at"]
                assert timedelta(minutes=14) < left <= ssp_tokens.PREV_TOKEN_GRACE

                assert await _bid(ek, tok2) == 200
                assert await _bid(ek, tok1) == 200  # окно ротации открыто

                # фид отдаёт prev-хэш, пока окно открыто
                r = await c.get("/rtb-feed/partners", headers=feed_hdr)
                assert r.status_code == 200
                fp = next(x for x in r.json()["partners"] if x["endpoint_key"] == ek)
                assert fp["token_prev_hash"] == ssp_tokens.hash_token(tok1)
                assert fp["token_prev_valid_until"] is not None

                # окно истекло -> старый токен отвергается, фид prev не отдаёт
                await conn.execute(
                    "UPDATE supply_partners SET token_prev_valid_until = now() - interval '1 second' WHERE id = $1", pid
                )
                assert await _bid(ek, tok1) == 403
                assert await _bid(ek, tok2) == 200
                r = await c.get("/rtb-feed/partners", headers=feed_hdr)
                fp = next(x for x in r.json()["partners"] if x["endpoint_key"] == ek)
                assert fp["token_prev_hash"] is None and fp["token_prev_valid_until"] is None

                # --- revoke: paused + revoked_at, хэши пусты, партнёр закрыт
                r = await c.delete(f"/supply-partners/{pid}/token")
                assert r.status_code == 204
                row = await conn.fetchrow("SELECT * FROM supply_partners WHERE id = $1", pid)
                assert row["status"] == "paused"
                assert row["token_revoked_at"] is not None
                assert row["auth_token_hash"] is None and row["token_prev_hash"] is None
                assert row["token_prev_valid_until"] is None
                r = await c.get(f"/supply-partners")
                me = next(x for x in r.json() if x["id"] == pid)
                assert me["has_auth_token"] is False and me["token_revoked_at"] is not None
                assert "token_prev_hash" not in me

                # paused не в фиде; даже если бы был active — hash пуст -> 403
                r = await c.get("/rtb-feed/partners", headers=feed_hdr)
                assert all(x["endpoint_key"] != ek for x in r.json()["partners"])
                await conn.execute("UPDATE supply_partners SET status = 'active' WHERE id = $1", pid)
                assert await _bid(ek, tok2) == 403
                assert await _bid(ek, None) == 403

                # --- revoke на draft-партнёре статус не трогает
                r = await c.post("/supply-partners", json={"name": "SSP Y"})
                pid2 = r.json()["id"]
                await c.post(f"/supply-partners/{pid2}/token")
                r = await c.delete(f"/supply-partners/{pid2}/token")
                assert r.status_code == 204
                assert await conn.fetchval("SELECT status FROM supply_partners WHERE id = $1", pid2) == "draft"
        finally:
            await conn.execute("DELETE FROM supply_partners WHERE user_id = $1", admin["id"])
            await conn.execute("DELETE FROM users WHERE id = $1", admin["id"])
            await conn.close()

    asyncio.run(scenario())


def test_migration_039_pauses_serving_partners_without_token(env):
    """Бэкфилл миграции: партнёр в testing/active без хэша уже переведён в paused.
    Проверяем идемпотентно повторным выполнением UPDATE из миграции."""

    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        admin = await _admin(conn)
        try:
            pid = await conn.fetchval(
                """INSERT INTO supply_partners (user_id, name, endpoint_key, status)
                   VALUES ($1, 'legacy', 'ek-' || gen_random_uuid()::text, 'active') RETURNING id""",
                admin["id"],
            )
            sql = (Path(__file__).resolve().parents[1] / "migrations" / "039_ssp_token_lifecycle.sql").read_text()
            await conn.execute(sql)
            assert await conn.fetchval("SELECT status FROM supply_partners WHERE id = $1", pid) == "paused"
        finally:
            await conn.execute("DELETE FROM supply_partners WHERE user_id = $1", admin["id"])
            await conn.execute("DELETE FROM users WHERE id = $1", admin["id"])
            await conn.close()

    asyncio.run(scenario())
