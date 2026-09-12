"""Audit O-05: /health — liveness, /ready — readiness с безопасной разбивкой.

Проверки зависимостей подменяются: тесты не поднимают PG/Redis, а фиксируют
контракт: какие состояния дают 200/503, что уходит в тело, что в gauges.
"""

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

import events_consumer
import main
import obs_metrics
import readiness


def _stub(monkeypatch, *, pg="ok", redis="ok", consumer=None):
    async def _pg():
        return pg

    async def _redis():
        return redis

    monkeypatch.setattr(readiness, "check_postgres", _pg)
    monkeypatch.setattr(readiness, "check_redis", _redis)
    if consumer is not None:
        monkeypatch.setattr(readiness, "check_events_consumer", lambda: consumer)


def _gauge(name: str) -> str | None:
    for line in obs_metrics.render_prometheus().splitlines():
        if line.startswith(name + " "):
            return line.split(" ")[1]
    return None


@pytest.fixture(autouse=True)
def _clean_metrics():
    obs_metrics.reset_for_tests()
    yield
    obs_metrics.reset_for_tests()


def test_ready_all_ok(monkeypatch):
    _stub(monkeypatch, consumer=readiness.OK)
    report = asyncio.run(readiness.evaluate())
    assert report.ready
    assert report.body() == {
        "status": "ok",
        "checks": {"postgres": "ok", "redis": "ok", "events_consumer": "ok"},
    }
    assert _gauge("backend_ready") == "1" and _gauge("backend_postgres_up") == "1" and _gauge("backend_redis_up") == "1"
    assert _gauge("rtb_events_consumer_up") == "1"


@pytest.mark.parametrize(
    "pg,redis,consumer,failing",
    [
        ("error", "ok", "ok", "postgres"),
        ("timeout", "ok", "ok", "postgres"),
        ("ok", "error", "ok", "redis"),
        ("ok", "timeout", "ok", "redis"),
        ("ok", "ok", "not_running", "events_consumer"),
        ("ok", "ok", "stale", "events_consumer"),
        ("ok", "ok", "lagging", "events_consumer"),
        ("ok", "ok", "starting", "events_consumer"),
    ],
)
def test_not_ready_reports_which_check_failed(monkeypatch, pg, redis, consumer, failing):
    _stub(monkeypatch, pg=pg, redis=redis, consumer=consumer)
    report = asyncio.run(readiness.evaluate())
    assert not report.ready
    body = report.body()
    assert body["status"] == "not_ready"
    assert body["checks"][failing] != "ok"
    assert _gauge("backend_ready") == "0"
    if failing == "events_consumer":
        assert _gauge("rtb_events_consumer_up") == "0"


def test_consumer_disabled_does_not_block_readiness(monkeypatch):
    _stub(monkeypatch, consumer=readiness.DISABLED)
    assert asyncio.run(readiness.evaluate()).ready


def test_redis_not_configured_is_ok_only_outside_production(monkeypatch):
    _stub(monkeypatch, redis="not_configured", consumer=readiness.DISABLED)
    monkeypatch.setenv("APP_ENV", "development")
    assert asyncio.run(readiness.evaluate()).ready
    monkeypatch.setenv("APP_ENV", "production")
    report = asyncio.run(readiness.evaluate())
    assert not report.ready and report.checks["redis"] == "not_configured"


def test_ready_endpoint_200_and_503(monkeypatch):
    monkeypatch.setenv("RTB_EVENTS_CONSUMER", "0")
    # lifespan требует REDIS_URL (durability check); недоступный адрес = warning, не отказ старта
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    import cache

    monkeypatch.setattr(cache, "_client", None)
    with TestClient(main.app) as client:
        _stub(monkeypatch, consumer=readiness.OK)
        r = client.get("/ready")
        assert r.status_code == 200 and r.json()["status"] == "ok"

        _stub(monkeypatch, redis="error", consumer=readiness.OK)
        r = client.get("/ready")
        assert r.status_code == 503
        assert r.json() == {
            "status": "not_ready",
            "checks": {"postgres": "ok", "redis": "error", "events_consumer": "ok"},
        }
        # liveness не зависит от зависимостей
        assert client.get("/health").status_code == 200


def test_ready_body_has_no_secrets(monkeypatch):
    # Реальный check_redis против несуществующего адреса: в тело уходит код,
    # а не текст исключения с host:port.
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    import cache

    monkeypatch.setattr(cache, "_client", None)
    monkeypatch.setattr(readiness, "REDIS_TIMEOUT_S", 0.3)
    state = asyncio.run(readiness.check_redis())
    assert state in ("error", "timeout")
    monkeypatch.setattr(cache, "_client", None)


def test_check_events_consumer_states(monkeypatch):
    monkeypatch.setenv("RTB_EVENTS_CONSUMER", "0")
    assert readiness.check_events_consumer() == "disabled"

    monkeypatch.setenv("RTB_EVENTS_CONSUMER", "1")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(events_consumer, "_task", None)
    assert readiness.check_events_consumer() == "not_running"

    class _Alive:
        def done(self):
            return False

    monkeypatch.setattr(events_consumer, "_task", _Alive())
    monkeypatch.setattr(events_consumer, "_last_ok_at", None)
    assert readiness.check_events_consumer() == "starting"

    import time

    monkeypatch.setattr(events_consumer, "_last_ok_at", time.monotonic() - 10_000)
    assert readiness.check_events_consumer() == "stale"

    monkeypatch.setattr(events_consumer, "_last_ok_at", time.monotonic())
    monkeypatch.setattr(events_consumer, "_last_lag", readiness.CONSUMER_MAX_LAG + 1)
    assert readiness.check_events_consumer() == "lagging"

    monkeypatch.setattr(events_consumer, "_last_lag", 5)
    assert readiness.check_events_consumer() == "ok"


def test_audit_skips_public_high_volume_posts():
    from audit import SKIP_PREFIXES

    for path in ("/rtb/win/x", "/rtb/imp/x", "/rtb/click/x", "/pixel/collect", "/ready", "/health", "/cron/rules"):
        assert path.startswith(SKIP_PREFIXES), path
    # управляющие ручки пикселя аудируются
    for path in ("/pixels", "/pixels/abc/goals", "/campaigns"):
        assert not path.startswith(SKIP_PREFIXES), path
    assert "/px/" not in SKIP_PREFIXES  # мёртвый префикс убран
