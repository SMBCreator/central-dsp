"""C6 / O-07 + F-01: пул под lock, graceful close, Cache-Control: no-store,
русская локализация 422."""
import asyncio

import pytest
from fastapi import FastAPI, Response
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, EmailStr, Field

import db
import validation_i18n
from http_hardening import NoStoreMiddleware, is_exempt


# --- db.get_pool: одна инициализация при параллельных вызовах ----------------

class _FakePool:
    def __init__(self):
        self.closed = False
        self.terminated = False

    async def close(self):
        self.closed = True

    def terminate(self):
        self.terminated = True


@pytest.fixture(autouse=True)
def _reset_pool_singleton(monkeypatch):
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_lock", None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@127.0.0.1:1/x")
    yield
    db._pool = None
    db._pool_lock = None


def test_concurrent_get_pool_creates_single_pool(monkeypatch):
    calls = {"n": 0}

    async def fake_create_pool(**_kw):
        calls["n"] += 1
        await asyncio.sleep(0.01)  # окно гонки: раньше каждый вызов создавал свой пул
        return _FakePool()

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)

    async def run():
        pools = await asyncio.gather(*(db.get_pool() for _ in range(8)))
        assert len({id(p) for p in pools}) == 1
        assert await db.get_pool() is pools[0]

    asyncio.run(run())
    assert calls["n"] == 1


def test_close_pool_is_idempotent_and_resets_singleton(monkeypatch):
    fake = _FakePool()

    async def fake_create_pool(**_kw):
        return fake

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)

    async def run():
        await db.get_pool()
        await db.close_pool()
        assert fake.closed and not fake.terminated
        assert db._pool is None
        await db.close_pool()  # второй вызов — no-op

    asyncio.run(run())


def test_close_pool_terminates_when_close_hangs(monkeypatch):
    class _Hanging(_FakePool):
        async def close(self):
            await asyncio.sleep(60)

    fake = _Hanging()

    async def fake_create_pool(**_kw):
        return fake

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(db.asyncio, "wait_for", lambda coro, timeout: _timeout(coro))

    async def run():
        await db.get_pool()
        await db.close_pool()
        assert fake.terminated

    asyncio.run(run())


async def _timeout(coro):
    coro.close()
    raise asyncio.TimeoutError


# --- Cache-Control: no-store ---------------------------------------------------

def _no_store_app() -> TestClient:
    app = FastAPI()

    @app.get("/auth/me")
    async def me():
        return {"id": 1}

    @app.get("/uploads/x.png")
    async def upload():
        return Response(b"png", media_type="image/png")

    @app.get("/stats/public")
    async def cached():
        return Response("ok", headers={"Cache-Control": "public, max-age=60"})

    @app.get("/boom")
    async def boom():
        raise RuntimeError("x")

    app.add_middleware(NoStoreMiddleware)
    return TestClient(app, raise_server_exceptions=False)


def test_no_store_added_to_api_responses():
    c = _no_store_app()
    assert c.get("/auth/me").headers["cache-control"] == "no-store"
    assert c.get("/missing").headers["cache-control"] == "no-store"  # 404 тоже персональный
    # Необработанное исключение -> 500 формирует ServerErrorMiddleware СНАРУЖИ
    # пользовательских мидлварей: заголовка там нет, но тело — константа
    # "Internal Server Error" без персональных данных.
    r = c.get("/boom")
    assert r.status_code == 500 and r.text == "Internal Server Error"


def test_no_store_respects_explicit_policy_and_exempt_paths():
    c = _no_store_app()
    assert c.get("/stats/public").headers["cache-control"] == "public, max-age=60"
    assert "cache-control" not in c.get("/uploads/x.png").headers


@pytest.mark.parametrize(
    "path,exempt",
    [
        ("/uploads/abc.png", True),
        ("/uploads", True),
        ("/health", True),
        ("/ready", True),
        ("/metrics", True),
        ("/rtb/bid", True),
        ("/rtb-feed/creatives", True),
        ("/pixel/collect", True),
        ("/uploadsX", False),
        ("/auth/me", False),
        ("/campaigns", False),
    ],
)
def test_is_exempt(path, exempt):
    assert is_exempt(path) is exempt


def test_main_app_has_no_store_and_validation_handler():
    import main

    assert any(m.cls is NoStoreMiddleware for m in main.app.user_middleware)
    assert RequestValidationError in main.app.exception_handlers


# --- 422 по-русски ------------------------------------------------------------

class _Body(BaseModel):
    name: str = Field(min_length=3, max_length=5)
    qty: int = Field(ge=1, le=10)
    email: EmailStr
    kind: str = Field(pattern=r"^(a|b)$")


def _validation_app() -> TestClient:
    app = FastAPI()
    validation_i18n.install(app)

    @app.post("/items")
    async def create(body: _Body):
        return {"ok": True}

    return TestClient(app)


def _errors(resp) -> dict[str, dict]:
    return {".".join(str(x) for x in e["loc"]): e for e in resp.json()["detail"]}


def test_422_messages_are_russian_and_do_not_echo_input():
    c = _validation_app()
    r = c.post("/items", json={"name": "ab", "qty": 0, "email": "nope", "kind": "zzz", "secret": "p@ss"})
    assert r.status_code == 422
    errs = _errors(r)
    assert errs["body.name"]["msg"] == "Минимум 3 символов"
    assert errs["body.qty"]["msg"] == "Значение должно быть не меньше 1"
    assert errs["body.email"]["msg"] == "Некорректный email"
    assert errs["body.kind"]["msg"] == "Недопустимый формат"
    for e in errs.values():
        assert set(e) == {"loc", "msg", "type"}  # без input/ctx/url
    assert "p@ss" not in r.text


def test_422_missing_and_type_errors():
    c = _validation_app()
    r = c.post("/items", json={"qty": "many", "email": "a@b.co", "kind": "a"})
    errs = _errors(r)
    assert errs["body.name"]["msg"] == "Обязательное поле"
    assert errs["body.name"]["type"] == "missing"
    assert errs["body.qty"]["msg"] == "Введите целое число"


def test_422_invalid_json_body():
    c = _validation_app()
    r = c.post("/items", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "Некорректный JSON"


def test_localize_message_value_error_prefix_and_unknown_type():
    assert (
        validation_i18n.localize_message({"type": "value_error", "msg": "Value error, Сумма не сходится"})
        == "Сумма не сходится"
    )
    assert validation_i18n.localize_message({"type": "totally_new", "msg": "Weird"}) == "Weird"
    assert (
        validation_i18n.localize_message({"type": "enum", "msg": "x", "ctx": {"expected": "'a' or 'b'"}})
        == "Недопустимое значение. Допустимые: 'a' or 'b'"
    )
