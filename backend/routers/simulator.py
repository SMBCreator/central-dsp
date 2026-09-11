"""SSP simulator: generates synthetic OpenRTB 2.5 BidRequests and runs the full
bid -> win -> impression -> click funnel against the bidder.

Audit E-04: every synthetic request carries OpenRTB ``test=1`` (§3.2.1). The
bid path therefore skips budget reservation and marks bidmeta/bid_events with
``is_test``; win/impression/click handlers keep the events in the journal but
do not bill and do not touch stats_hourly — a demo run from the cabinet can no
longer distort a client's spend, CTR or win-rate.

Engine selection (C1d) is explicit and never silently changes:

* ``engine=go`` (default) — the production Go bidder over HTTP, the same path
  SSPs hit. If it is unreachable the run fails with 503 and
  ``fallback_used=false``; the Python reference is never substituted, because
  a simulation that does not exercise the real bidder is not a diagnostic.
* ``engine=python_reference`` — the in-process Python reference
  implementation used as the parity oracle. Available only outside production
  and only when ``SIMULATOR_ALLOW_PYTHON_REFERENCE`` is set; in production the
  mode does not exist (404).
"""
import logging
import os
import random
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import obs_metrics
import ssp_check
from auth import current_user
from db import get_pool
from routers.rtb import click, impression, process_bid_request, win_notice
from startup_checks import is_production
from tokens import sign_event_token

router = APIRouter(prefix="/simulator", tags=["simulator"])
log = logging.getLogger("simulator")

Engine = Literal["go", "python_reference"]

COUNTRIES = ["RUS", "KAZ", "UZB", "BLR", "KGZ"]
DOMAINS = ["news.example.ru", "sport.example.kz", "auto.example.uz", "tech.example.by", "media.example.kg"]
OS_LIST = ["android", "ios", "windows"]


class SimulateIn(BaseModel):
    endpoint_key: str
    requests: int = Field(default=10, ge=1, le=200)
    ad_type: str = Field(default="banner", pattern="^(banner|native|video|mixed)$")
    win_probability: float = Field(default=0.6, ge=0, le=1)
    ctr: float = Field(default=0.02, ge=0, le=1)
    geo_country: str | None = None
    device_type: str | None = Field(default=None, pattern="^(mobile|desktop|ctv)$")


class GoBidderUnavailable(Exception):
    """Go bidder answered 503 / was unreachable — the run is refused, not rerouted."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def python_reference_allowed() -> bool:
    """Python reference engine: only outside production and only when explicitly enabled."""
    if is_production():
        return False
    return os.getenv("SIMULATOR_ALLOW_PYTHON_REFERENCE", "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_engine(requested: Engine):
    """Return (callable, engine) for the requested engine or raise.

    python_reference in production -> 404 (the mode does not exist there);
    outside production without the flag -> 403 with the flag name.
    """
    if requested == "go":
        return ssp_check.go_bidder_caller(), "go"
    if is_production():
        raise HTTPException(status_code=404, detail="Unknown simulator engine")
    if not python_reference_allowed():
        raise HTTPException(
            status_code=403,
            detail="Python reference engine is disabled. Set SIMULATOR_ALLOW_PYTHON_REFERENCE=1 (development/test only).",
        )

    async def python_reference(endpoint_key: str, req: dict, **_):
        return await process_bid_request(endpoint_key, req)

    return python_reference, "python_reference"


def _make_bid_request(ad_type: str, geo: str | None, device: str | None) -> dict:
    if ad_type == "mixed":
        ad_type = random.choice(["banner", "native", "video"])
    imp: dict = {"id": "1", "bidfloor": round(random.uniform(0.1, 1.5), 2)}
    if ad_type == "banner":
        w, h = random.choice([(300, 250), (728, 90), (320, 50), (300, 600)])
        imp["banner"] = {"w": w, "h": h, "format": [{"w": w, "h": h}]}
    elif ad_type == "native":
        imp["native"] = {"request": "{\"ver\":\"1.2\"}"}
    else:
        imp["video"] = {"mimes": ["video/mp4"], "minduration": 5, "maxduration": 60, "w": 640, "h": 360}

    devicetype = {"mobile": 1, "desktop": 2, "ctv": 3}.get(device or random.choice(["mobile", "desktop"]), 1)
    return {
        "id": str(uuid.uuid4()),
        # OpenRTB 3.2.1: test mode — auctions are not billable. Bidder stores
        # is_test in bidmeta/bid_events; tracking handlers skip billing/stats.
        "test": 1,
        "imp": [imp],
        "site": {"domain": random.choice(DOMAINS), "page": "https://" + random.choice(DOMAINS)},
        "device": {
            "devicetype": devicetype,
            "os": random.choice(OS_LIST),
            "ifa": str(uuid.uuid4()),
            "ip": f"185.{random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}",
            "geo": {"country": geo or random.choice(COUNTRIES)},
        },
        "user": {"id": str(uuid.uuid4())},
        "at": 2,
        "tmax": 300,
        "cur": ["USD"],
    }


async def call_engine(bidder, engine: str, endpoint_key: str, req: dict):
    """One bid call with latency accounting. Go 503 -> GoBidderUnavailable (never rerouted)."""
    started = time.monotonic()
    try:
        return await bidder(endpoint_key, req, verify_token=False)
    except HTTPException as e:
        if engine == "go" and e.status_code == 503:
            raise GoBidderUnavailable(str(e.detail)) from e
        raise
    finally:
        obs_metrics.observe_simulator_latency(engine, time.monotonic() - started)


def _log_run(request: Request, user: dict, body: SimulateIn, requested: str, actual: str | None,
             outcome: str, go_status: str | None = None) -> None:
    log.info(
        "simulator run",
        extra={
            "requested_engine": requested,
            "actual_engine": actual,
            "fallback_used": False,
            "outcome": outcome,
            "go_bidder_status": go_status,
            "caller_id": user.get("id"),
            "tenant_id": user.get("agency_id"),
            "endpoint_key": body.endpoint_key,
            "request_id": request.headers.get("x-request-id"),
            "environment": os.getenv("APP_ENV", ""),
        },
    )


@router.post("/run")
async def run_simulation(
    body: SimulateIn,
    request: Request,
    engine: Engine = Query(default="go"),
    user: dict = Depends(current_user),
):
    bidder, actual_engine = resolve_engine(engine)

    pool = await get_pool()
    partner = await pool.fetchrow(
        "SELECT id FROM supply_partners WHERE endpoint_key = $1 AND user_id = $2",
        body.endpoint_key, user["id"],
    )
    if not partner:
        raise HTTPException(status_code=404, detail="SSP-партнёр с таким ключом не найден")

    results = {"requests": 0, "bids": 0, "no_bids": 0, "wins": 0, "impressions": 0, "clicks": 0, "errors": 0}
    sample_response = None

    for _ in range(body.requests):
        results["requests"] += 1
        bid_req = _make_bid_request(body.ad_type, body.geo_country, body.device_type)
        try:
            data = await call_engine(bidder, actual_engine, body.endpoint_key, bid_req)
        except GoBidderUnavailable as e:
            obs_metrics.inc("simulator_go_unavailable_total")
            _log_run(request, user, body, engine, None, "go_unavailable", go_status=e.detail)
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Go bidder is unavailable. Python reference fallback is disabled"
                              + (" in production." if is_production() else "; select engine=python_reference explicitly if you need the reference run."),
                    "engine": "go",
                    "requested_engine": engine,
                    "fallback_used": False,
                },
            )
        except HTTPException:
            raise
        except Exception:
            results["errors"] += 1
            continue
        if data is None:
            results["no_bids"] += 1
            continue
        if sample_response is None:
            sample_response = {"request": bid_req, "response": data}
        results["bids"] += 1

        the_bid = data["seatbid"][0]["bid"][0]
        if random.random() < body.win_probability:
            clear = round(float(the_bid["price"]) * random.uniform(0.7, 1.0), 4)
            # Event handlers require HMAC-signed tokens (tokens.py); Go and the
            # Python reference share the secret, so signing here matches either.
            event_token = sign_event_token(the_bid["id"])
            try:
                await win_notice(bid=event_token, price=clear)
                results["wins"] += 1
                await impression(bid=event_token)
                results["impressions"] += 1
                if random.random() < body.ctr:
                    await click(bid=event_token)
                    results["clicks"] += 1
            except Exception:
                results["errors"] += 1

    obs_metrics.inc_simulator_request(actual_engine)
    if actual_engine == "python_reference":
        obs_metrics.inc("simulator_reference_requests_total")
    _log_run(request, user, body, engine, actual_engine, "ok")

    return {
        "results": results,
        "sample": sample_response,
        "engine": actual_engine,
        "requested_engine": engine,
        "fallback_used": False,
        # Все события помечены is_test: в отчётах клиента их нет, в bid_events — есть.
        "test": True,
    }
