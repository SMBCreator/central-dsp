"""C1d guard: SIMULATOR_ALLOW_PYTHON_REFERENCE never opens the Python reference
engine in production, and Go-unavailable never reroutes to it anywhere.

This file is the single place that states the invariant end-to-end; the
scenario tests in test_simulator.py / test_startup_checks.py stay as-is.

1. production + SIMULATOR_ALLOW_PYTHON_REFERENCE=true -> startup check refuses to start.
2. production: engine=python_reference is 404 regardless of the flag value.
3. non-production: python_reference only with the flag explicitly truthy (else 403).
4. production + Go down -> 503, engine=go, fallback_used=false, reference untouched.
5. no environment/flag combination turns Go-unavailable into a Python reference run.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, HTTPException

import obs_metrics
import startup_checks as sc
from auth import current_user
from routers import simulator as sim

FLAG = "SIMULATOR_ALLOW_PYTHON_REFERENCE"
USER = {"id": 7, "role": "admin", "agency_id": 3}
PROD_OK = {
    "APP_ENV": "production",
    "EVENT_TOKEN_SECRET": "a" * 64,
    "CRON_SECRET": "b" * 40,
    "RTB_API_TOKEN": "c" * 32,
    "DATABASE_URL": "postgresql://dsp:" + "d" * 24 + "@postgres:5432/dsp",
}
TRUTHY = ("1", "true", "True", "yes", "on")
FALSY_OR_UNSET = (None, "", "0", "false", "no", "off")


class _Pool:
    async def fetchrow(self, *_a, **_k):
        return {"id": 1}


def _env(monkeypatch, *, production: bool, flag: str | None):
    monkeypatch.setattr(sim, "is_production", lambda env=None: production)
    if flag is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, flag)


def _client(monkeypatch):
    async def _pool():
        return _Pool()

    monkeypatch.setattr(sim, "get_pool", _pool)
    app = FastAPI()
    app.include_router(sim.router)

    async def _user():
        return USER

    app.dependency_overrides[current_user] = _user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _go(monkeypatch, *, fail: BaseException | None = None):
    """Fake Go bidder: answer with a bid or raise `fail`."""
    def caller(client=None):
        async def call(key, req, **kw):
            if fail is not None:
                raise fail
            return {"id": req["id"], "seatbid": [{"bid": [{"id": "b1", "price": 1.0}]}]}
        return call
    monkeypatch.setattr(sim.ssp_check, "go_bidder_caller", caller)


def _python(monkeypatch) -> list:
    """Spy on the reference engine; returns the list of requests it received."""
    seen: list = []

    async def ref(key, req, **kw):
        seen.append(req)
        return None
    monkeypatch.setattr(sim, "process_bid_request", ref)
    return seen


def _run(monkeypatch, engine: str | None = None):
    q = f"?engine={engine}" if engine else ""

    async def go():
        async with _client(monkeypatch) as c:
            return await c.post(f"/simulator/run{q}", json={"endpoint_key": "k", "requests": 2, "win_probability": 0})
    return asyncio.run(go())


@pytest.fixture(autouse=True)
def _reset_metrics():
    obs_metrics.reset_for_tests()
    yield
    obs_metrics.reset_for_tests()


# 1. startup: production refuses to start with the flag on ---------------------

@pytest.mark.parametrize("value", TRUTHY)
def test_1_production_startup_refuses_flag(value):
    env = dict(PROD_OK, **{FLAG: value})
    with pytest.raises(RuntimeError) as ei:
        sc.assert_production_config(env)
    assert FLAG in str(ei.value)
    assert "bidder-go" in str(ei.value)


@pytest.mark.parametrize("value", FALSY_OR_UNSET)
def test_1_production_startup_accepts_flag_off(value):
    env = dict(PROD_OK)
    if value is not None:
        env[FLAG] = value
    sc.assert_production_config(env)  # не бросает


# 2. production: python_reference does not exist, whatever the flag says --------

@pytest.mark.parametrize("value", TRUTHY + FALSY_OR_UNSET)
def test_2_production_python_reference_is_404_regardless_of_flag(monkeypatch, value):
    _env(monkeypatch, production=True, flag=value)
    _go(monkeypatch)
    py_seen = _python(monkeypatch)

    assert sim.python_reference_allowed() is False
    with pytest.raises(HTTPException) as ei:
        sim.resolve_engine("python_reference")
    assert ei.value.status_code == 404

    r = _run(monkeypatch, engine="python_reference")
    assert r.status_code == 404
    assert py_seen == []
    assert obs_metrics.snapshot()["simulator_requests"]["python_reference"] == 0


# 3. non-production: reference only behind an explicit truthy flag -------------

@pytest.mark.parametrize("value", FALSY_OR_UNSET)
def test_3_dev_python_reference_denied_without_explicit_flag(monkeypatch, value):
    _env(monkeypatch, production=False, flag=value)
    _go(monkeypatch)
    py_seen = _python(monkeypatch)

    assert sim.python_reference_allowed() is False
    r = _run(monkeypatch, engine="python_reference")
    assert r.status_code == 403
    assert FLAG in r.json()["detail"]
    assert py_seen == []


@pytest.mark.parametrize("value", TRUTHY)
def test_3_dev_python_reference_allowed_with_explicit_flag(monkeypatch, value):
    _env(monkeypatch, production=False, flag=value)
    _go(monkeypatch)
    py_seen = _python(monkeypatch)

    assert sim.python_reference_allowed() is True
    r = _run(monkeypatch, engine="python_reference")
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "python_reference" and body["requested_engine"] == "python_reference"
    assert body["fallback_used"] is False
    assert len(py_seen) == 2


def test_3_dev_flag_is_a_warning_not_a_startup_failure():
    env = dict(PROD_OK, APP_ENV="development", **{FLAG: "1"})
    sc.assert_production_config(env)  # не бросает вне production


# 4. production + Go down -> 503 without fallback ------------------------------

def test_4_production_go_unavailable_is_503_no_fallback(monkeypatch):
    _env(monkeypatch, production=True, flag=None)
    _go(monkeypatch, fail=HTTPException(status_code=503, detail="go down"))
    py_seen = _python(monkeypatch)

    r = _run(monkeypatch)
    assert r.status_code == 503
    body = r.json()
    assert body["engine"] == "go"
    assert body["requested_engine"] == "go"
    assert body["fallback_used"] is False
    assert "disabled in production" in body["detail"]
    assert py_seen == []
    snap = obs_metrics.snapshot()
    assert snap["counters"]["simulator_go_unavailable_total"] == 1
    assert snap["simulator_requests"]["python_reference"] == 0


# 5. no scenario turns Go-unavailable into a Python reference run --------------

@pytest.mark.parametrize("production", (True, False))
@pytest.mark.parametrize("flag", TRUTHY + FALSY_OR_UNSET)
def test_5_go_503_never_reroutes_to_reference(monkeypatch, production, flag):
    _env(monkeypatch, production=production, flag=flag)
    _go(monkeypatch, fail=HTTPException(status_code=503, detail="go down"))
    py_seen = _python(monkeypatch)

    r = _run(monkeypatch)  # default engine=go
    assert r.status_code == 503
    body = r.json()
    assert body["engine"] == "go" and body["fallback_used"] is False
    assert py_seen == []
    assert obs_metrics.snapshot()["simulator_requests"]["python_reference"] == 0


@pytest.mark.parametrize("production", (True, False))
def test_5_go_transport_error_never_reroutes_to_reference(monkeypatch, production):
    """A non-HTTP failure (connection refused etc.) is counted as an error, not rerouted."""
    _env(monkeypatch, production=production, flag="1")
    _go(monkeypatch, fail=httpx.ConnectError("refused"))
    py_seen = _python(monkeypatch)

    r = _run(monkeypatch)
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "go" and body["fallback_used"] is False
    assert body["results"]["errors"] == 2 and body["results"]["bids"] == 0
    assert py_seen == []


def test_5_resolve_engine_go_ignores_flag(monkeypatch):
    """engine=go always resolves to the Go caller, even when the reference is allowed."""
    _env(monkeypatch, production=False, flag="1")
    sentinel = object()
    monkeypatch.setattr(sim.ssp_check, "go_bidder_caller", lambda client=None: sentinel)
    bidder, engine = sim.resolve_engine("go")
    assert bidder is sentinel and engine == "go"
