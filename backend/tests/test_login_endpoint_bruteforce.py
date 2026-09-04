"""HTTP-уровень /auth/login (D5): 429 после N неудач по логину и по IP,
Retry-After, X-Forwarded-For как источник IP за прокси, успех сбрасывает
счётчик по логину. Без Postgres — pool подменён; Redis — fakeredis."""

import sys
from pathlib import Path

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cache  # noqa: E402
import routers.auth_router as ar  # noqa: E402
from auth import hash_password  # noqa: E402

GOOD_HASH = hash_password("correct-horse")


class _FakePool:
    async def fetchrow(self, sql, email):
        if email == "alice@x.ru":
            return {"id": "00000000-0000-0000-0000-000000000001", "email": email, "name": "Alice",
                    "role": "manager", "status": "active", "password_hash": GOOD_HASH}
        return None


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(cache, "_client", fakeredis.aioredis.FakeRedis(decode_responses=True))
    monkeypatch.setattr(cache, "LOGIN_FAILS_PER_EMAIL", 3)
    monkeypatch.setattr(cache, "LOGIN_FAILS_PER_IP", 6)

    async def _pool():
        return _FakePool()

    async def _session(uid):
        return "tok-" + uid, None

    monkeypatch.setattr(ar, "get_pool", _pool)
    monkeypatch.setattr(ar, "create_session", _session)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ar.router)
    return TestClient(app)


def _login(client, email, password, ip="198.51.100.1"):
    return client.post("/auth/login", json={"email": email, "password": password},
                       headers={"X-Forwarded-For": ip})


def test_wrong_password_then_block_by_login(client):
    for _ in range(3):
        assert _login(client, "alice@x.ru", "nope").status_code == 401
    r = _login(client, "alice@x.ru", "nope")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    # Даже с ПРАВИЛЬНЫМ паролем во время блокировки — 429 (иначе перебор
    # узнал бы пароль по смене кода ответа).
    assert _login(client, "alice@x.ru", "correct-horse").status_code == 429


def test_unknown_login_counts_and_blocks_same_way(client):
    """Несуществующий логин ведёт себя как существующий — список users не раскрывается."""
    for _ in range(3):
        assert _login(client, "ghost@x.ru", "x").status_code == 401
    assert _login(client, "ghost@x.ru", "x").status_code == 429


def test_block_by_ip_across_many_logins(client):
    for i in range(6):
        assert _login(client, f"u{i}@x.ru", "x", ip="203.0.113.9").status_code == 401
    # 7-я попытка с этого IP — блок, хотя логин новый.
    assert _login(client, "new@x.ru", "x", ip="203.0.113.9").status_code == 429
    # С другого IP тот же логин — можно.
    assert _login(client, "new@x.ru", "x", ip="203.0.113.10").status_code == 401


def test_success_resets_login_counter(client):
    _login(client, "alice@x.ru", "nope")
    _login(client, "alice@x.ru", "nope")
    ok = _login(client, "alice@x.ru", "correct-horse")
    assert ok.status_code == 200 and ok.json()["email"] == "alice@x.ru"
    # Счётчик по логину сброшен: снова 3 попытки до блока.
    for _ in range(3):
        assert _login(client, "alice@x.ru", "nope").status_code == 401
    assert _login(client, "alice@x.ru", "nope").status_code == 429


def test_xff_first_hop_is_used(client):
    """За Caddy RemoteAddr = прокси; клиентский IP берётся из первого XFF."""
    for i in range(6):
        assert _login(client, f"a{i}@x.ru", "x", ip="10.0.0.1, 127.0.0.1").status_code == 401
    assert _login(client, "b@x.ru", "x", ip="10.0.0.1").status_code == 429
    assert _login(client, "b@x.ru", "x", ip="10.0.0.2").status_code == 401
