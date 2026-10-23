"""ID sync (план predeploy A.8): полный цикл через HTTP + PostgreSQL + Redis.

Админ настраивает SSP -> SSP зовёт /sync/{id}/in -> соответствие в PG и Redis
-> фид биддера несёт identity/соль -> повтор, opt-out, удаление, open redirect,
replay подписи /out, rebuild/purge. Флаг выключен -> 404 на всех ручках.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg  # noqa: E402
import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

import cache  # noqa: E402
import db  # noqa: E402
import obs_metrics  # noqa: E402
import ssp_check  # noqa: E402
from auth import current_user  # noqa: E402
from routers import bidder_feed, cron, id_sync as sync_router, supply_partners as sp_router  # noqa: E402

CALLBACK = "https://sync.ssp.example/match?dsp=centraldsp&uid={dsp_uid}"


@pytest.fixture
def env(monkeypatch):
    db._pool = None
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cache, "_client", fake)

    async def _noop(client=None):
        return False

    monkeypatch.setattr(ssp_check, "request_feed_reload", _noop)
    monkeypatch.setenv("RTB_API_TOKEN", "feed-token-test")
    monkeypatch.setenv("ID_SYNC_ENABLED", "1")
    monkeypatch.setenv("ID_SYNC_SECRET", "x" * 48)
    monkeypatch.setenv("ID_SYNC_BASE_URL", "https://dsp.example")
    monkeypatch.delenv("CRON_SECRET", raising=False)
    obs_metrics.reset_for_tests()
    yield fake
    db._pool = None


def _app(user_row: dict) -> FastAPI:
    app = FastAPI()
    for r in (sp_router.router, bidder_feed.router, sync_router.router, cron.router):
        app.include_router(r)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    # https: cookie dsp_uid — Secure, по http клиент её бы не отправил.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://dsp.example")


async def _admin(conn) -> dict:
    uid = await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','adm','admin') RETURNING id",
        f"idsync-{uuid.uuid4()}@test.local",
    )
    return {"id": uid, "email": "adm@x", "name": "adm", "role": "admin"}


async def _partner(c: httpx.AsyncClient, **identity) -> dict:
    cfg = {"sync_enabled": True, "sync_callback_url_template": CALLBACK,
           "sync_redirect_hosts": ["sync.ssp.example"], "requires_consent": True}
    cfg.update(identity)
    r = await c.post("/supply-partners", json={"name": f"SSP {uuid.uuid4().hex[:6]}", "identity": cfg})
    assert r.status_code == 201, r.text
    p = r.json()
    assert "id_sync_salt" not in p and p["identity"]["sync_enabled"] is True
    assert (await c.post(f"/supply-partners/{p['id']}/token")).status_code == 200
    r = await c.patch(f"/supply-partners/{p['id']}", json={"status": "testing"})
    assert r.status_code == 200, r.text
    return p


def test_full_cycle(env):
    fake = env

    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        admin = await _admin(conn)
        try:
            app = _app(admin)
            async with _client(app) as c:
                p = await _partner(c)
                pid = p["id"]
                salt = await conn.fetchval("SELECT id_sync_salt FROM supply_partners WHERE id = $1", pid)
                assert salt and len(salt) == 64

                # PATCH identity не меняет соль (иначе все соответствия потеряются).
                r = await c.patch(f"/supply-partners/{pid}", json={"identity": {**p["identity"], "ttl_days": 30}})
                assert r.status_code == 200, r.text
                assert await conn.fetchval("SELECT id_sync_salt FROM supply_partners WHERE id = $1", pid) == salt

                # --- без согласия: gif, записи нет
                r = await c.get(f"/sync/{pid}/in", params={"ssp_uid": "u-1"})
                assert r.status_code == 200 and r.headers["content-type"] == "image/gif"
                assert r.headers["cache-control"].startswith("no-store")
                assert r.headers["referrer-policy"] == "no-referrer"
                assert "set-cookie" not in r.headers
                assert await conn.fetchval("SELECT count(*) FROM id_sync_map WHERE partner_id = $1", pid) == 0

                # --- SSP -> DSP: соответствие + 302 на callback с нашим dsp_uid
                r = await c.get(f"/sync/{pid}/in", params={"ssp_uid": "u-1", "gdpr": "1", "gdpr_consent": "CPabc"})
                assert r.status_code == 302, r.text
                loc = urlsplit(r.headers["location"])
                assert loc.hostname == "sync.ssp.example"
                dsp_uid = parse_qs(loc.query)["uid"][0]
                assert len(dsp_uid) == 32
                sc = r.headers["set-cookie"].lower()
                assert "dsp_uid=" in sc and "secure" in sc and "httponly" in sc and "samesite=none" in sc and "path=/sync" in sc
                row = await conn.fetchrow("SELECT * FROM id_sync_map WHERE partner_id = $1", pid)
                uid_hash = hashlib.sha256(salt.encode() + b"u-1").digest()
                assert bytes(row["ssp_uid_hash"]) == uid_hash and row["dsp_uid"] == dsp_uid
                assert row["consent_scope"] == "gdpr"
                assert b"u-1" not in bytes(row["ssp_uid_hash"])
                assert await fake.get(f"idsync:{pid}:{uid_hash.hex()}") == dsp_uid
                assert 0 < await fake.ttl(f"idsync:{pid}:{uid_hash.hex()}") <= 30 * 86400

                # --- фид биддера несёт identity (объектом) и соль
                r = await c.get("/rtb-feed/partners", headers={"Authorization": "Bearer feed-token-test"})
                fp = next(x for x in r.json()["partners"] if str(x["id"]) == pid)
                assert fp["identity"]["sync_enabled"] is True and fp["id_sync_salt"] == salt

                # --- повтор с cookie: тот же dsp_uid, refreshed
                r = await c.get(f"/sync/{pid}/in", params={"ssp_uid": "u-1", "gdpr_consent": "CPabc"})
                assert r.status_code == 302
                assert parse_qs(urlsplit(r.headers["location"]).query)["uid"][0] == dsp_uid

                # --- новый браузер без cookie, тот же ssp_uid: dsp_uid переиспользуется
                async with _client(app) as fresh:
                    r = await fresh.get(f"/sync/{pid}/in", params={"ssp_uid": "u-1", "gdpr_consent": "CPabc"})
                    assert parse_qs(urlsplit(r.headers["location"]).query)["uid"][0] == dsp_uid

                # --- мусорные/опасные ID: gif, ничего не записано
                before = await conn.fetchval("SELECT count(*) FROM id_sync_map")
                for bad in ("a" * 129, "x\r\nSet-Cookie: s=1", "x' OR 1=1--", "0000"):
                    async with _client(app) as fresh:
                        r = await fresh.get(f"/sync/{pid}/in", params={"ssp_uid": bad, "gdpr_consent": "c"})
                        assert r.status_code == 200 and "set-cookie" not in r.headers
                assert await conn.fetchval("SELECT count(*) FROM id_sync_map") == before

                # --- неизвестный партнёр: тот же ответ, без раскрытия
                r = await c.get(f"/sync/{uuid.uuid4()}/in", params={"ssp_uid": "u-1", "gdpr_consent": "c"})
                assert r.status_code == 200 and r.headers["content-type"] == "image/gif"
                r = await c.get("/sync/not-a-uuid/in", params={"ssp_uid": "u-1"})
                assert r.status_code == 200

                # --- open redirect: JSON подменён в обход API -> gif, метрика
                await conn.execute(
                    """UPDATE supply_partners SET identity = jsonb_set(identity, '{sync_callback_url_template}',
                       '"https://evil.example/?u={dsp_uid}"') WHERE id = $1""", pid)
                r = await c.get(f"/sync/{pid}/in", params={"ssp_uid": "u-2", "gdpr_consent": "c"})
                assert r.status_code == 200 and "location" not in r.headers
                assert "id_sync_redirect_rejected_total 1" in obs_metrics.render_prometheus()
                await conn.execute(
                    "UPDATE supply_partners SET identity = jsonb_set(identity, '{sync_callback_url_template}', to_jsonb($2::text)) WHERE id = $1",
                    pid, CALLBACK)

                # --- DSP -> SSP: подписанная ссылка /out, повтор подписи отклоняется
                r = await c.get(f"/supply-partners/{pid}/sync-urls")
                urls = r.json()
                assert urls["enabled_globally"] and urls["secret_configured"] and urls["active_mappings"] == 2
                assert urls["in_url"].startswith(f"https://dsp.example/sync/{pid}/in?ssp_uid=")
                out_path = urls["out_url"].replace("https://dsp.example", "")
                r = await c.get(out_path)
                assert r.status_code == 302 and parse_qs(urlsplit(r.headers["location"]).query)["uid"][0] == dsp_uid
                r = await c.get(out_path)
                assert r.status_code == 200 and "location" not in r.headers  # replay
                r = await c.get(f"/sync/{pid}/out", params={"ts": "1", "sig": "0" * 64})
                assert r.status_code == 200 and "location" not in r.headers

                # --- rebuild: Redis потерян -> досев из PG
                await fake.flushall()
                r = await c.get("/cron/rebuild-idsync")
                assert r.status_code == 200 and r.json()["written"] >= 2
                assert await fake.get(f"idsync:{pid}:{uid_hash.hex()}") == dsp_uid

                # --- purge: просроченные удаляются
                u2_hash = hashlib.sha256(salt.encode() + b"u-2").digest()
                await conn.execute("UPDATE id_sync_map SET expires_at = now() - interval '1 day' WHERE partner_id = $1 AND ssp_uid_hash = $2", pid, u2_hash)
                r = await c.get("/cron/id-sync-purge")
                assert r.status_code == 200 and r.json()["deleted"] >= 1
                assert await conn.fetchval("SELECT count(*) FROM id_sync_map WHERE partner_id = $1", pid) == 1

                # --- opt-out: соответствия удалены (PG + Redis), cookie = optout, новые не создаются
                r = await c.get("/sync/optout")
                assert r.status_code == 200 and "dsp_uid=optout" in r.headers["set-cookie"]
                assert await conn.fetchval("SELECT count(*) FROM id_sync_map WHERE dsp_uid = $1", dsp_uid) == 0
                assert await fake.get(f"idsync:{pid}:{uid_hash.hex()}") is None
                r = await c.get(f"/sync/{pid}/in", params={"ssp_uid": "u-9", "gdpr_consent": "c"})
                assert r.status_code == 200 and "set-cookie" not in r.headers
                assert await conn.fetchval("SELECT count(*) FROM id_sync_map WHERE partner_id = $1", pid) == 0

                metrics = obs_metrics.render_prometheus()
                assert f'id_sync_requests_total{{partner="{pid}",status="mapped"}}' in metrics
                assert f'id_sync_mappings_created_total{{partner="{pid}"}}' in metrics
                assert "id_sync_consent_denied_total 1" in metrics
        finally:
            await conn.execute("DELETE FROM users WHERE id = $1", admin["id"])
            await conn.close()

    asyncio.run(scenario())


def test_delete_endpoint_and_consent_free_partner(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        admin = await _admin(conn)
        try:
            async with _client(_app(admin)) as c:
                p = await _partner(c, requires_consent=False, sync_callback_url_template=None)
                r = await c.get(f"/sync/{p['id']}/in", params={"ssp_uid": "zz"})
                assert r.status_code == 200 and r.headers["content-type"] == "image/gif"  # без callback — gif
                row = await conn.fetchrow("SELECT dsp_uid, consent_scope FROM id_sync_map WHERE partner_id = $1", p["id"])
                assert row["consent_scope"] == "none"
                r = await c.post("/sync/delete")
                assert r.status_code == 204
                assert await conn.fetchval("SELECT count(*) FROM id_sync_map WHERE partner_id = $1", p["id"]) == 0
        finally:
            await conn.execute("DELETE FROM users WHERE id = $1", admin["id"])
            await conn.close()

    asyncio.run(scenario())


def test_flag_off_routes_404_and_cron_noop(env, monkeypatch):
    monkeypatch.setenv("ID_SYNC_ENABLED", "0")

    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        admin = await _admin(conn)
        try:
            async with _client(_app(admin)) as c:
                pid = str(uuid.uuid4())
                for method, path in (("GET", f"/sync/{pid}/in"), ("GET", f"/sync/{pid}/out"),
                                     ("GET", "/sync/optout"), ("POST", "/sync/delete")):
                    r = await c.request(method, path)
                    assert r.status_code == 404, path
                    assert "set-cookie" not in r.headers
                assert (await c.get("/cron/id-sync-purge")).json() == {"skipped": "disabled"}
                assert (await c.get("/cron/rebuild-idsync")).json() == {"skipped": "disabled"}
            assert "id_sync_requests_total{" not in obs_metrics.render_prometheus()
        finally:
            await conn.execute("DELETE FROM users WHERE id = $1", admin["id"])
            await conn.close()

    asyncio.run(scenario())
