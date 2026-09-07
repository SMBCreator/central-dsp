"""Audit E-04: симулятор SSP шлёт OpenRTB test=1 и идёт через Go-биддер,
падая на встроенный Python только когда Go недоступен (503)."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException

from routers import simulator as sim


def test_every_synthetic_request_is_test_mode():
    for ad_type in ("banner", "native", "video", "mixed"):
        req = sim._make_bid_request(ad_type, None, None)
        assert req["test"] == 1, ad_type
        assert req["imp"] and req["id"]


def test_pick_bidder_prefers_go_and_reuses_probe(monkeypatch):
    calls = []

    def fake_caller():
        async def call(key, req, **kw):
            calls.append((key, req["test"], kw.get("verify_token")))
            return {"seatbid": [{"bid": [{"id": "b1", "price": 1.0}]}]}
        return call

    monkeypatch.setattr(sim.ssp_check, "go_bidder_caller", fake_caller)

    async def boom(*a, **k):
        raise AssertionError("Python bidder must not be used when Go answers")
    monkeypatch.setattr(sim, "process_bid_request", boom)

    req = sim._make_bid_request("banner", None, None)
    bidder, mode, data = asyncio.run(sim._pick_bidder("key", req))
    assert mode == "go" and data["seatbid"]
    # Один HTTP-вызов: probe-запрос не выбрасывается, а идёт в статистику прогона.
    assert calls == [("key", 1, False)]


def test_pick_bidder_falls_back_to_python_on_503(monkeypatch):
    def fake_caller():
        async def call(key, req, **kw):
            raise HTTPException(status_code=503, detail="Go-биддер недоступен")
        return call

    monkeypatch.setattr(sim.ssp_check, "go_bidder_caller", fake_caller)
    seen = []

    async def py(key, req, **kw):
        seen.append(req["test"])
        return None
    monkeypatch.setattr(sim, "process_bid_request", py)

    bidder, mode, data = asyncio.run(sim._pick_bidder("key", sim._make_bid_request("banner", None, None)))
    assert mode == "python" and data is None and seen == [1]


def test_pick_bidder_propagates_non_503(monkeypatch):
    """404/403 от Go — реальная ошибка партнёра, а не «Go недоступен»: не маскируем."""
    def fake_caller():
        async def call(key, req, **kw):
            raise HTTPException(status_code=404, detail="unknown endpoint")
        return call

    monkeypatch.setattr(sim.ssp_check, "go_bidder_caller", fake_caller)
    try:
        asyncio.run(sim._pick_bidder("key", sim._make_bid_request("banner", None, None)))
    except HTTPException as e:
        assert e.status_code == 404
    else:
        raise AssertionError("expected HTTPException(404)")
