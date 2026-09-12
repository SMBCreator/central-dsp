"""E-05: ingestion пикселя — схема и лимиты до БД, rate limit до БД, канонический URL скрипта.

Настоящий router + fake pool: считаем обращения к БД, чтобы доказать, что мусор
и заблокированные запросы не стоят ни одного SELECT'а.
"""
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.pixel as px
import routers.settings as settings_mod

PIXEL_ID = str(uuid.uuid4())
OWNER_ID = str(uuid.uuid4())


class _FakePool:
    def __init__(self):
        self.calls: list[str] = []
        self.inserted: list[tuple] = []

    async def fetchval(self, sql, *args):
        self.calls.append(sql)
        if "FROM pixels" in sql:
            return OWNER_ID if args and args[0] == PIXEL_ID else None
        return None

    async def fetch(self, sql, *args):
        self.calls.append(sql)
        return []  # нет целей

    async def fetchrow(self, sql, *args):
        self.calls.append(sql)
        return None

    async def execute(self, sql, *args):
        self.calls.append(sql)
        if "INSERT INTO pixel_events" in sql:
            self.inserted.append(args)


@pytest.fixture
def env(monkeypatch):
    pool = _FakePool()

    async def _pool():
        return pool

    accepted: dict = {"drop": None, "seen": 0}

    async def _guard(**kw):
        accepted["seen"] += 1
        return accepted["drop"]

    async def _noop_segments(*a, **k):
        return None

    monkeypatch.setattr(px, "get_pool", _pool)
    monkeypatch.setattr(px.pixel_guard, "should_accept", _guard)
    monkeypatch.setattr(px, "process_pixel_event", _noop_segments)
    app = FastAPI()
    app.include_router(px.router)
    return TestClient(app), pool, accepted


def _post(client, payload, headers=None):
    raw = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    return client.post("/pixel/collect", content=raw, headers=headers or {})


def _valid(**over):
    base = {"pid": PIXEL_ID, "sid": "s1", "type": "pageview", "url": "https://shop.ru/",
            "sw": 1920, "ua": "Mozilla/5.0 Chrome/120", "dur": 0, "sp": 0}
    base.update(over)
    return base


class TestGarbageIsCheapAnd204:
    def test_json_array_no_500_no_db(self, env):
        client, pool, guard = env
        r = _post(client, "[]")
        assert r.status_code == 204
        assert r.headers.get("x-pixel-drop") == "invalid"
        assert pool.calls == [] and guard["seen"] == 0

    def test_not_json(self, env):
        client, pool, _ = env
        assert _post(client, b"\xff\xfe not json").status_code == 204
        assert pool.calls == []

    @pytest.mark.parametrize("bad", [
        {"pid": "not-a-uuid", "sid": "s"},
        {"pid": PIXEL_ID, "sid": ""},
        {"pid": PIXEL_ID, "sid": "s", "type": "purchase"},
        {"pid": PIXEL_ID, "sid": "x" * 65},
        {"pid": PIXEL_ID, "sid": "s", "url": "u" * 501},
        {"pid": PIXEL_ID, "sid": "s", "clid": {"nested": 1}},
        {"sid": "s"},
    ])
    def test_schema_violations_dropped_before_db(self, env, bad):
        client, pool, guard = env
        r = _post(client, bad)
        assert r.status_code == 204 and r.headers["x-pixel-drop"] == "invalid"
        assert pool.calls == [] and guard["seen"] == 0

    def test_oversize_by_header_is_not_read(self, env):
        client, pool, _ = env
        r = _post(client, _valid(), headers={"content-length": str(px.MAX_COLLECT_BODY + 1)})
        assert r.status_code == 204 and r.headers["x-pixel-drop"] == "too_large"
        assert pool.calls == []

    def test_oversize_body_without_trusting_header(self):
        raw = json.dumps(_valid(ua="A" * (px.MAX_COLLECT_BODY + 10))).encode()
        assert px.parse_collect(raw) is None

    def test_numbers_are_clamped_not_rejected(self):
        ev = px.parse_collect(json.dumps(_valid(dur="999", sp=150.7, sw=-5)).encode())
        assert ev is not None
        assert (ev.dur, ev.sp, ev.sw) == (60, 100, 0)


class TestOrderOfChecks:
    def test_rate_limited_request_never_touches_db(self, env):
        client, pool, guard = env
        guard["drop"] = px.pixel_guard.DROP_IP_LIMIT
        r = _post(client, _valid())
        assert r.status_code == 204 and r.headers["x-pixel-drop"] == "ip_limit"
        assert guard["seen"] == 1
        assert pool.calls == []

    def test_unknown_pixel_costs_one_select_and_no_insert(self, env):
        client, pool, _ = env
        r = _post(client, _valid(pid=str(uuid.uuid4())))
        assert r.status_code == 204 and r.headers["x-pixel-drop"] == "unknown_pixel"
        assert len(pool.calls) == 1 and "FROM pixels" in pool.calls[0]
        assert pool.inserted == []

    def test_valid_event_is_stored(self, env):
        client, pool, _ = env
        r = _post(client, _valid(name="", clid="bid-1"))
        assert r.status_code == 204 and "x-pixel-drop" not in r.headers
        assert len(pool.inserted) == 1
        args = pool.inserted[0]
        assert args[0] == PIXEL_ID and args[2] == "pageview"
        assert args[3] is None          # пустая строка name -> NULL
        assert args[11] == "bid-1"
        assert r.headers["access-control-allow-origin"] == "*"


class TestScriptUrl:
    def test_uses_public_api_base_not_request_host(self, env, monkeypatch):
        client, pool, _ = env
        monkeypatch.setenv("PUBLIC_API_BASE_URL", "https://api.dsp.example/")
        r = client.get(f"/pixel/script/{PIXEL_ID}.js", headers={"host": "backend:8000"})
        assert r.status_code == 200
        assert 'var API = "https://api.dsp.example/pixel/collect";' in r.text
        assert "backend:8000" not in r.text

    def test_falls_back_to_public_base_url_plus_api(self, monkeypatch):
        monkeypatch.delenv("PUBLIC_API_BASE_URL", raising=False)
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://dsp.example")
        assert settings_mod.public_api_base_url() == "https://dsp.example/api"

    def test_no_public_url_means_404_not_guess(self, env, monkeypatch):
        client, pool, _ = env
        monkeypatch.delenv("PUBLIC_API_BASE_URL", raising=False)
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        r = client.get(f"/pixel/script/{PIXEL_ID}.js")
        assert r.status_code == 404
        assert pool.calls == []  # даже pixel не ищем

    def test_bad_uuid_is_404_without_db(self, env, monkeypatch):
        client, pool, _ = env
        monkeypatch.setenv("PUBLIC_API_BASE_URL", "https://api.dsp.example")
        assert client.get("/pixel/script/../etc.js").status_code in (404, 400)
        assert pool.calls == []
