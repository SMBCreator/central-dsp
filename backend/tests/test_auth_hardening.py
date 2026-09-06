"""Audit S-04: bootstrap/config/auth edges.

- пароль > 72 байт UTF-8 -> 422 в схемах (раньше 500 из bcrypt);
- hash/verify не падают на длинных строках;
- смена пароля отзывает остальные сессии, текущую оставляет;
- сброс пароля / блокировка админом отзывает все сессии цели;
- два конкурентных register на пустой БД -> ровно один superadmin;
- REGISTRATION_MODE default closed: второй register -> 403.

DB-тесты требуют DATABASE_URL (в CI — сервис postgres).
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth  # noqa: E402
import schemas  # noqa: E402

# 25 кириллических символов = 50 байт (ок), 37 = 74 байта (>72)
CYR_OK = "п" * 25
CYR_LONG = "п" * 37
ASCII_73 = "a" * 73

# --- схемы ----------------------------------------------------------------------


@pytest.mark.parametrize("pw", [CYR_LONG, ASCII_73, "x" * 128])
def test_register_password_over_72_bytes_is_422(pw):
    with pytest.raises(ValidationError) as ei:
        schemas.RegisterIn(email="a@b.co", password=pw, name="n")
    assert "72" in str(ei.value)


@pytest.mark.parametrize("pw", [CYR_OK, "a" * 72, "12345678"])
def test_register_password_within_limit_ok(pw):
    assert schemas.RegisterIn(email="a@b.co", password=pw, name="n").password == pw


def test_change_password_new_password_limited_current_not():
    with pytest.raises(ValidationError):
        schemas.ChangePasswordIn(current_password="whatever", new_password=ASCII_73)
    # current_password проверяется через verify -> False, не 422 (не раскрываем лимит)
    schemas.ChangePasswordIn(current_password="c" * 100, new_password="newpass123")


def test_admin_user_schemas_share_limit():
    import routers.share as share
    import routers.users_router as ur

    with pytest.raises(ValidationError):
        ur.UserCreate(email="a@b.co", name="n", password=ASCII_73, role="manager")
    with pytest.raises(ValidationError):
        ur.UserPatch(password=ASCII_73)
    with pytest.raises(ValidationError):
        share.GuestIn(email="a@b.co", password=ASCII_73, name="n")
    assert ur.UserPatch(password=None).password is None


# --- auth.py ------------------------------------------------------------------------


def test_hash_password_rejects_over_limit_and_verify_returns_false():
    with pytest.raises(ValueError):
        auth.hash_password(ASCII_73)
    h = auth.hash_password("correct-horse")
    assert auth.verify_password("correct-horse", h)
    assert auth.verify_password(ASCII_73, h) is False
    assert auth.verify_password(CYR_LONG, h) is False


def test_async_wrappers_roundtrip():
    async def run():
        h = await auth.hash_password_async(CYR_OK)
        assert await auth.verify_password_async(CYR_OK, h)
        assert not await auth.verify_password_async(CYR_OK + "x", h)

    asyncio.run(run())


def test_startup_registration_mode_used_by_router():
    src = (Path(__file__).resolve().parents[1] / "routers" / "auth_router.py").read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" in src
    assert 'os.environ.get("REGISTRATION_MODE"' not in src
    assert "registration_mode()" in src


# --- DB -----------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark_db = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def _client(app):
    import httpx

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://dsp.example.com")


@pytestmark_db
def test_change_password_revokes_other_sessions_keeps_current(monkeypatch):
    import asyncpg
    from fastapi import FastAPI

    import db
    import routers.auth_router as ar

    monkeypatch.setenv("APP_ENV", "dev")
    db._pool = None

    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        email = f"s04-{uuid.uuid4()}@example.com"
        uid = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1,$2,'s04','manager') RETURNING id",
            email, auth.hash_password("old-password-1"),
        )
        try:
            app = FastAPI()
            app.include_router(ar.router)
            c = _client(app)
            # три сессии: две «чужие» + одна текущая
            t1, _ = await auth.create_session(str(uid))
            t2, _ = await auth.create_session(str(uid))
            cur, _ = await auth.create_session(str(uid))

            r = await c.post(
                "/auth/change-password",
                json={"current_password": "old-password-1", "new_password": "new-password-2"},
                cookies={auth.COOKIE_NAME: cur},
            )
            assert r.status_code == 200, r.text
            assert r.json()["revoked_sessions"] == 2

            rows = await conn.fetch("SELECT token FROM sessions WHERE user_id = $1", uid)
            assert {r["token"] for r in rows} == {cur}

            # старый пароль больше не работает, новый — да
            r = await c.post("/auth/login", json={"email": email, "password": "old-password-1"})
            assert r.status_code == 401
            r = await c.post("/auth/login", json={"email": email, "password": "new-password-2"})
            assert r.status_code == 200, r.text
            # неверный текущий пароль -> 400, сессии не трогаем
            r = await c.post(
                "/auth/change-password",
                json={"current_password": "wrong", "new_password": "another-pass-3"},
                cookies={auth.COOKIE_NAME: cur},
            )
            assert r.status_code == 400
            # 73-байтный новый пароль -> 422, не 500
            r = await c.post(
                "/auth/change-password",
                json={"current_password": "new-password-2", "new_password": ASCII_73},
                cookies={auth.COOKIE_NAME: cur},
            )
            assert r.status_code == 422, r.text
        finally:
            await conn.execute("DELETE FROM users WHERE id = $1", uid)
            await conn.close()
            db._pool = None

    asyncio.run(run())


@pytestmark_db
def test_admin_password_reset_and_block_revoke_all_sessions(monkeypatch):
    import asyncpg
    from fastapi import FastAPI

    import db
    import routers.users_router as ur
    from auth import current_user

    db._pool = None

    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        admin = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','adm','superadmin') RETURNING id",
            f"s04-adm-{uuid.uuid4()}@example.com",
        )
        victim = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','v','manager') RETURNING id",
            f"s04-v-{uuid.uuid4()}@example.com",
        )
        try:
            app = FastAPI()
            app.include_router(ur.router)

            async def _admin():
                return {"id": str(admin), "email": "a@x", "name": "adm", "role": "superadmin",
                        "status": "active", "permissions": {}}

            app.dependency_overrides[current_user] = _admin
            c = _client(app)

            await auth.create_session(str(victim))
            await auth.create_session(str(victim))
            r = await c.patch(f"/users/{victim}", json={"password": "reset-by-admin-1"})
            assert r.status_code == 200, r.text
            assert await conn.fetchval("SELECT count(*) FROM sessions WHERE user_id = $1", victim) == 0

            await auth.create_session(str(victim))
            r = await c.patch(f"/users/{victim}", json={"status": "blocked"})
            assert r.status_code == 200, r.text
            assert await conn.fetchval("SELECT count(*) FROM sessions WHERE user_id = $1", victim) == 0

            # смена имени сессии не трогает
            await auth.create_session(str(victim))
            r = await c.patch(f"/users/{victim}", json={"name": "renamed"})
            assert r.status_code == 200
            assert await conn.fetchval("SELECT count(*) FROM sessions WHERE user_id = $1", victim) == 1

            r = await c.patch(f"/users/{victim}", json={"password": ASCII_73})
            assert r.status_code == 422
        finally:
            await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [admin, victim])
            await conn.close()
            db._pool = None

    asyncio.run(run())


@pytestmark_db
def test_concurrent_register_on_populated_db_never_grants_superadmin(monkeypatch):
    """Не требует пустой users: 4 параллельных register при open -> все manager,
    superadmin никому; при closed -> все 403 и ни одной строки."""
    import asyncpg
    from fastapi import FastAPI

    import db
    import routers.auth_router as ar

    monkeypatch.setenv("APP_ENV", "dev")
    db._pool = None

    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        seed = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','seed','superadmin') RETURNING id",
            f"seed-{uuid.uuid4()}@example.com",
        )
        emails = [f"pop-{i}-{uuid.uuid4()}@example.com" for i in range(4)]
        try:
            app = FastAPI()
            app.include_router(ar.router)
            c = _client(app)

            async def reg(e):
                return await c.post("/auth/register", json={"email": e, "password": "bootstrap-pass-1", "name": "b"})

            monkeypatch.setenv("REGISTRATION_MODE", "closed")
            results = await asyncio.gather(*(reg(e) for e in emails))
            assert all(r.status_code == 403 for r in results)
            assert await conn.fetchval("SELECT count(*) FROM users WHERE email = ANY($1::text[])", emails) == 0

            monkeypatch.setenv("REGISTRATION_MODE", "open")
            results = await asyncio.gather(*(reg(e) for e in emails))
            assert all(r.status_code == 200 for r in results), [(r.status_code, r.text) for r in results]
            roles = await conn.fetch("SELECT role FROM users WHERE email = ANY($1::text[])", emails)
            assert [r["role"] for r in roles] == ["manager"] * 4
        finally:
            await conn.execute("DELETE FROM users WHERE id = $1 OR email = ANY($2::text[])", seed, emails)
            await conn.close()
            db._pool = None

    asyncio.run(run())


@pytestmark_db
def test_concurrent_bootstrap_yields_single_superadmin(monkeypatch):
    """Четыре register одновременно на пустой БД: ровно один superadmin, остальные
    403 (default closed). Требует пустой users — на CI так; локально skip."""
    import asyncpg
    from fastapi import FastAPI

    import db
    import routers.auth_router as ar

    monkeypatch.delenv("REGISTRATION_MODE", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    db._pool = None

    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        # Сериализуем с другими DB-тестами, которые могли бы создать users параллельно.
        await conn.execute("SELECT pg_advisory_lock(424242)")
        try:
            # На CI users пуста; локально — могут быть строки. Переносим в сторону.
            # FK на users каскадные (advertisers.user_id и т.п.) — если есть данные,
            # тест пропускаем, а не ломаем локальную БД.
            n_users = await conn.fetchval("SELECT count(*) FROM users")
            if n_users:
                pytest.skip("users не пуста — bootstrap-тест только на чистой БД (CI)")

            app = FastAPI()
            app.include_router(ar.router)
            c = _client(app)
            emails = [f"boot-{i}-{uuid.uuid4()}@example.com" for i in range(4)]

            async def reg(e):
                return await c.post("/auth/register", json={"email": e, "password": "bootstrap-pass-1", "name": "b"})

            results = await asyncio.gather(*(reg(e) for e in emails))
            codes = sorted(r.status_code for r in results)
            assert codes == [200, 403, 403, 403], [(r.status_code, r.text) for r in results]
            roles = await conn.fetch("SELECT role FROM users WHERE email = ANY($1::text[])", emails)
            assert [r["role"] for r in roles] == ["superadmin"]

            # После bootstrap: closed -> 403, open -> manager
            r = await c.post("/auth/register", json={"email": f"x-{uuid.uuid4()}@example.com", "password": "bootstrap-pass-1", "name": "b"})
            assert r.status_code == 403
            monkeypatch.setenv("REGISTRATION_MODE", "open")
            fresh = f"boot-open-{uuid.uuid4()}@example.com"
            r = await c.post("/auth/register", json={"email": fresh, "password": "bootstrap-pass-1", "name": "b"})
            assert r.status_code == 200 and r.json()["role"] == "manager"
            # дубликат email в open-режиме -> 409, не 500
            r = await c.post("/auth/register", json={"email": fresh, "password": "bootstrap-pass-1", "name": "b"})
            assert r.status_code == 409
        finally:
            await conn.execute("DELETE FROM users WHERE email LIKE 'boot-%@example.com' OR email LIKE 'x-%@example.com'")
            await conn.execute("SELECT pg_advisory_unlock(424242)")
            await conn.close()
            db._pool = None

    asyncio.run(run())
