"""Audit F-02 / B14b (DB): backfill миграции 044 и отзыв доступов при блокировке.

Требует DATABASE_URL (pytest.skip иначе). Миграция уже применена CI-раннером;
здесь создаём строки «до B14b» (created_by_user_id IS NULL) и повторно выполняем
файл — он должен быть идемпотентен и посчитать snapshot как цену владельца:
цепочка (1+a)(1+b) для agency/client, 1.0 для остальных ролей.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

MIG = Path(__file__).resolve().parents[1] / "migrations" / "044_share_markup_snapshot.sql"


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class _Pool:
    def __init__(self, conn):
        self._c = conn

    async def execute(self, *a):
        return await self._c.execute(*a)

    async def fetchrow(self, *a):
        return await self._c.fetchrow(*a)

    async def fetch(self, *a):
        return await self._c.fetch(*a)

    async def fetchval(self, *a):
        return await self._c.fetchval(*a)


async def _mk_user(conn, role, parent=None, markup=0):
    return await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role, parent_user_id, markup_pct) "
        "VALUES ($1, 'x', $2, $3, $4, $5) RETURNING id",
        f"b14b-{uuid.uuid4().hex[:10]}@t.local", role, role, parent, markup,
    )


def test_migration_044_backfill_and_idempotency():
    async def main():
        conn = await asyncpg.connect(DATABASE_URL)
        tag = uuid.uuid4().hex[:8]
        try:
            admin = await _mk_user(conn, "admin")
            agency = await _mk_user(conn, "agency", parent=admin, markup=20)
            client = await _mk_user(conn, "client", parent=agency, markup=10)
            ids = {}
            for owner, key in ((admin, "admin"), (agency, "agency"), (client, "client")):
                ids[key] = await conn.fetchval(
                    "INSERT INTO share_links (user_id, token, name, revoked) VALUES ($1, $2, $3, $4) RETURNING id",
                    owner, f"tok-{tag}-{key}", key, key == "admin",
                )
            # строки «до B14b»: created_by пуст, snapshot дефолтный
            await conn.execute("UPDATE share_links SET created_by_user_id = NULL WHERE user_id = ANY($1::uuid[])",
                               [admin, agency, client])
            guest_user = await _mk_user(conn, "viewer")
            await conn.execute(
                "INSERT INTO guest_access (user_id, owner_id) VALUES ($1, $2)", guest_user, client
            )
            await conn.execute("UPDATE guest_access SET created_by_user_id = NULL WHERE user_id = $1", guest_user)

            await conn.execute(MIG.read_text())

            rows = {r["name"]: r for r in await conn.fetch(
                "SELECT name, created_by_user_id, markup_mult_snapshot, markup_mode, revoked, revoked_at "
                "FROM share_links WHERE user_id = ANY($1::uuid[])", [admin, agency, client])}
            assert float(rows["admin"]["markup_mult_snapshot"]) == 1.0
            assert float(rows["agency"]["markup_mult_snapshot"]) == pytest.approx(1.2)
            assert float(rows["client"]["markup_mult_snapshot"]) == pytest.approx(1.32)
            for r in rows.values():
                assert r["created_by_user_id"] is not None and r["markup_mode"] == "inherited"
            assert rows["admin"]["revoked_at"] is not None and rows["agency"]["revoked_at"] is None

            g = await conn.fetchrow("SELECT markup_mult_snapshot, created_by_user_id FROM guest_access WHERE user_id = $1",
                                    guest_user)
            assert float(g["markup_mult_snapshot"]) == pytest.approx(1.32)
            assert g["created_by_user_id"] == client

            # повторный прогон: строки уже с created_by — snapshot не меняется даже после правки markup_pct
            await conn.execute("UPDATE users SET markup_pct = 50 WHERE id = $1", agency)
            await conn.execute(MIG.read_text())
            again = await conn.fetchval("SELECT markup_mult_snapshot FROM share_links WHERE id = $1", ids["client"])
            assert float(again) == pytest.approx(1.32)
        finally:
            await conn.execute("DELETE FROM users WHERE email LIKE 'b14b-%@t.local'")
            await conn.close()

    _run(main())


def test_block_owner_revokes_links_guests_and_guest_sessions(monkeypatch):
    import auth
    import db
    from routers import share

    async def main():
        conn = await asyncpg.connect(DATABASE_URL)
        pool = _Pool(conn)

        async def gp():
            return pool
        monkeypatch.setattr(db, "get_pool", gp)
        monkeypatch.setattr(auth, "get_pool", gp)
        monkeypatch.setattr(share, "get_pool", gp)
        try:
            admin = await _mk_user(conn, "admin")
            agency = await _mk_user(conn, "agency", parent=admin, markup=20)
            link_id = await conn.fetchval(
                "INSERT INTO share_links (user_id, created_by_user_id, token, name) VALUES ($1, $1, $2, 'l') RETURNING id",
                agency, f"tok-{uuid.uuid4().hex[:8]}",
            )
            guest = await _mk_user(conn, "viewer")
            await conn.execute(
                "INSERT INTO guest_access (user_id, owner_id, created_by_user_id) VALUES ($1, $2, $2)", guest, agency
            )
            await conn.execute(
                "INSERT INTO sessions (user_id, token, expires_at) VALUES ($1, $2, now() + interval '1 day')",
                guest, f"sess-{uuid.uuid4().hex}",
            )

            await share.revoke_all_access_of(str(agency), str(admin))

            link = await conn.fetchrow("SELECT revoked, revoked_at, revoked_by_user_id FROM share_links WHERE id = $1",
                                       link_id)
            assert link["revoked"] is True and link["revoked_at"] is not None
            assert link["revoked_by_user_id"] == admin
            ga = await conn.fetchrow("SELECT revoked_at, revoked_by_user_id FROM guest_access WHERE user_id = $1", guest)
            assert ga["revoked_at"] is not None and ga["revoked_by_user_id"] == admin
            assert await conn.fetchval("SELECT count(*) FROM sessions WHERE user_id = $1", guest) == 0

            # публичный токен после отзыва — 404
            from fastapi import HTTPException
            tok = await conn.fetchval("SELECT token FROM share_links WHERE id = $1", link_id)
            with pytest.raises(HTTPException) as ei:
                await share._resolve_link(tok)
            assert ei.value.status_code == 404
        finally:
            await conn.execute("DELETE FROM users WHERE email LIKE 'b14b-%@t.local'")
            await conn.close()

    _run(main())
