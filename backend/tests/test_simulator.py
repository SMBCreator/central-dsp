"""C1d: simulator engine selection is explicit and never falls back.

* engine=go is the only engine in production; Go down -> 503, fallback_used=false.
* engine=python_reference exists only outside production and only behind
  SIMULATOR_ALLOW_PYTHON_REFERENCE; in production the mode is a 404.
* Every response (success or 503) states which engine actually answered.
Audit E-04 (every synthetic request is OpenRTB test=1) stays covered here.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, HTTPException

import obs_metrics
from auth import current_user
from routers import simulator as sim

USER = {"id": 7, "role": "admin", "agency_id": 3}


class _Pool:
    async def fetchrow(self, *_a, **_k):
        return {"id": 1}


def _client(monkeypatch, *, production: bool, allow_reference: bool = False):
    monkeypatch.setattr(sim, "is_production", lambda env=None: production)
    if allow_reference:
        monkeypatch.setenv("SIMULATOR_ALLOW_PYTHON_REFERENCE", "1")
    else:
        monkeypatch.delenv("SIMULATOR_ALLOW_PYTHON_REFERENCE", raising=False)

    async def _pool():
        return _Pool()

    monkeypatch.setattr(sim, "get_pool", _pool)
    app = FastAPI()
    app.include_router(sim.router)

    async def _user():
        return USER

    app.dependency_overrides[current_user] = _user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _go(monkeypatch, *, status: int | None = None, seen: list | None = None):
    """Fake ssp_check.go_bidder_caller: raise `status` or answer with a bid."""
    def caller(client=None):
        async def call(key, req, **kw):
            if seen is not None:
                seen.append(req)
            if status is not None:
                raise HTTPException(status_code=status, detail=f"fake go {status}")
            return {"id": req["id"], "seatbid": [{"bid": [{"id": "b1", "price": 1.0}]}]}
        return call
    monkeypatch.setattr(sim.ssp_check, "go_bidder_caller", caller)


def _python(monkeypatch, seen: list):
    async def ref(key, req, **kw):
        seen.append(req)
        return None
    monkeypatch.setattr(sim, "process_bid_request", ref)


def _post(client, engine: str | None = None, requests: int = 3):
    q = f"?engine={engine}" if engine else ""
    return client.post(f"/simulator/run{q}", json={"endpoint_key": "k", "requests": requests, "win_probability": 0})


@pytest.fixture(autouse=True)
def _reset_metrics():
    obs_metrics.reset_for_tests()
    yield
    obs_metrics.reset_for_tests()


def test_every_synthetic_request_is_test_mode():
    for ad_type in ("banner", "native", "video", "mixed"):
        req = sim._make_bid_request(ad_type, None, None)
        assert req["test"] == 1, ad_type
        assert req["imp"] and req["id"]


# --------------------------------------------------------------- production ---

def test_production_go_unavailable_is_503_without_fallback(monkeypatch):
    py_seen: list = []
    _python(monkeypatch, py_seen)
    _go(monkeypatch, status=503)

    async def go():
        async with _client(monkeypatch, production=True) as c:
            return await _post(c)
    r = asyncio.run(go())

    assert r.status_code == 503
    body = r.json()
    assert body["engine"] == "go" and body["fallback_used"] is False
    assert "Python reference fallback is disabled in production" in body["detail"]
    # security: тело 503 не раскрывает внутренний адрес биддера и технические детали
    assert "go_bidder_status" not in body
    assert "fake go 503" not in r.text and "http://" not in r.text
    assert py_seen == []  # the reference engine was never touched
    assert obs_metrics.snapshot()["counters"]["simulator_go_unavailable_total"] == 1
    assert "simulator_go_unavailable_total 1" in obs_metrics.render_prometheus()


def test_production_python_reference_mode_does_not_exist(monkeypatch):
    monkeypatch.setenv("SIMULATOR_ALLOW_PYTHON_REFERENCE", "1")  # flag must be ignored in production
    _go(monkeypatch)

    async def go():
        async with _client(monkeypatch, production=True) as c:
            monkeypatch.setenv("SIMULATOR_ALLOW_PYTHON_REFERENCE", "1")
            return await _post(c, engine="python_reference")
    r = asyncio.run(go())
    assert r.status_code == 404
    assert sim.python_reference_allowed() is False


def test_production_success_reports_go_and_no_fallback(monkeypatch):
    seen: list = []
    _go(monkeypatch, seen=seen)

    async def go():
        async with _client(monkeypatch, production=True) as c:
            return await _post(c, requests=2)
    r = asyncio.run(go())
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "go" and body["requested_engine"] == "go" and body["fallback_used"] is False
    assert body["results"]["bids"] == 2 and len(seen) == 2
    assert all(req["test"] == 1 for req in seen)
    assert obs_metrics.snapshot()["simulator_requests"]["go"] == 1
    assert obs_metrics.snapshot()["simulator_latency"]["go"][0] == 2


# -------------------------------------------------------------- development ---

def test_dev_go_mode_calls_go_bidder(monkeypatch):
    seen: list = []
    py_seen: list = []
    _go(monkeypatch, seen=seen)
    _python(monkeypatch, py_seen)

    async def go():
        async with _client(monkeypatch, production=False, allow_reference=True) as c:
            return await _post(c, engine="go", requests=2)
    r = asyncio.run(go())
    assert r.status_code == 200 and r.json()["engine"] == "go"
    assert len(seen) == 2 and py_seen == []


def test_dev_go_unavailable_does_not_switch_engine(monkeypatch):
    py_seen: list = []
    _go(monkeypatch, status=503)
    _python(monkeypatch, py_seen)

    async def go():
        async with _client(monkeypatch, production=False, allow_reference=True) as c:
            return await _post(c)
    r = asyncio.run(go())
    assert r.status_code == 503
    assert r.json()["fallback_used"] is False and py_seen == []


def test_dev_python_reference_requires_explicit_flag(monkeypatch):
    _go(monkeypatch)
    py_seen: list = []
    _python(monkeypatch, py_seen)

    async def go():
        async with _client(monkeypatch, production=False, allow_reference=False) as c:
            return await _post(c, engine="python_reference")
    r = asyncio.run(go())
    assert r.status_code == 403
    assert "SIMULATOR_ALLOW_PYTHON_REFERENCE" in r.json()["detail"]
    assert py_seen == []


def test_dev_python_reference_explicit_mode(monkeypatch):
    go_seen: list = []
    py_seen: list = []
    _go(monkeypatch, seen=go_seen)
    _python(monkeypatch, py_seen)

    async def go():
        async with _client(monkeypatch, production=False, allow_reference=True) as c:
            return await _post(c, engine="python_reference", requests=3)
    r = asyncio.run(go())
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "python_reference" and body["requested_engine"] == "python_reference"
    assert body["fallback_used"] is False and body["results"]["no_bids"] == 3
    assert len(py_seen) == 3 and go_seen == []
    snap = obs_metrics.snapshot()
    assert snap["simulator_requests"] == {"go": 0, "python_reference": 1}
    assert snap["counters"]["simulator_reference_requests_total"] == 1
    assert 'simulator_requests_total{engine="python_reference"} 1' in obs_metrics.render_prometheus()


def test_unknown_engine_rejected(monkeypatch):
    _go(monkeypatch)

    async def go():
        async with _client(monkeypatch, production=False) as c:
            return await _post(c, engine="java")
    assert asyncio.run(go()).status_code == 422


def test_go_non_503_errors_are_not_masked(monkeypatch):
    """404/403 from Go is a partner problem, not 'Go unavailable' — surfaced as-is."""
    _go(monkeypatch, status=404)

    async def go():
        async with _client(monkeypatch, production=True) as c:
            return await _post(c)
    r = asyncio.run(go())
    assert r.status_code == 404 and "fallback_used" not in r.json()


def test_simulator_requires_authenticated_user():
    route = next(r for r in sim.router.routes if getattr(r, "path", "") == "/simulator/run")
    assert any(d.call is current_user for d in route.dependant.dependencies)
