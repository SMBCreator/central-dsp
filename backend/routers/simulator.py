"""SSP simulator: generates synthetic OpenRTB 2.5 BidRequests and runs the full
bid -> win -> impression -> click funnel against the bidder.

Audit E-04: every synthetic request carries OpenRTB ``test=1`` (§3.2.1). The
bid path therefore skips budget reservation and marks bidmeta/bid_events with
``is_test``; win/impression/click handlers keep the events in the journal but
do not bill and do not touch stats_hourly — a demo run from the cabinet can no
longer distort a client's spend, CTR or win-rate.

The bid step goes through the production Go bidder over HTTP when it is
reachable (the same path SSPs hit), falling back to the in-process Python
bidder otherwise; ``mode`` in the response tells which one answered.
"""
import random
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import ssp_check
from auth import current_user
from db import get_pool
from routers.rtb import click, impression, process_bid_request, win_notice
from tokens import sign_event_token

router = APIRouter(prefix="/simulator", tags=["simulator"])

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


async def _python_bidder(endpoint_key: str, req: dict, **_):
    return await process_bid_request(endpoint_key, req)


async def _pick_bidder(endpoint_key: str, first_req: dict):
    """Go bidder over HTTP if reachable (production path), else in-process Python.

    Returns (callable, mode, first_response). The probe is a real test=1
    request, so its answer is reused rather than wasted.
    """
    go_call = ssp_check.go_bidder_caller()
    try:
        data = await go_call(endpoint_key, first_req, verify_token=False)
        return go_call, "go", data
    except HTTPException as e:
        if e.status_code != 503:
            raise
    # Go bidder unavailable (dev/CI without the container) — Python path.
    return _python_bidder, "python", await _python_bidder(endpoint_key, first_req)


@router.post("/run")
async def run_simulation(body: SimulateIn, user: dict = Depends(current_user)):
    pool = await get_pool()
    partner = await pool.fetchrow(
        "SELECT id FROM supply_partners WHERE endpoint_key = $1 AND user_id = $2",
        body.endpoint_key, user["id"],
    )
    if not partner:
        raise HTTPException(status_code=404, detail="SSP-партнёр с таким ключом не найден")

    results = {"requests": 0, "bids": 0, "no_bids": 0, "wins": 0, "impressions": 0, "clicks": 0, "errors": 0}
    sample_response = None
    bidder = None
    mode = None

    for _ in range(body.requests):
        results["requests"] += 1
        bid_req = _make_bid_request(body.ad_type, body.geo_country, body.device_type)
        try:
            if bidder is None:
                bidder, mode, data = await _pick_bidder(body.endpoint_key, bid_req)
            else:
                data = await bidder(body.endpoint_key, bid_req, verify_token=False)
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
            # Event handlers require HMAC-signed tokens (tokens.py); Go and
            # Python share the secret, so signing here matches either bidder.
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

    return {
        "results": results,
        "sample": sample_response,
        "mode": mode or "python",
        # Все события помечены is_test: в отчётах клиента их нет, в bid_events — есть.
        "test": True,
    }
