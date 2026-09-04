"""ssp_check.go_bidder_caller — адаптер автопроверки SSP к боевому Go-биддеру.

Контракт адаптера = контракту process_bid_request (dict / None / HTTPException),
чтобы run_connection_check работала одинаково. Go эмулируем httpx.MockTransport.
"""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ssp_check  # noqa: E402


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


def _run(coro):
    return asyncio.run(coro)


def test_200_returns_json_and_sends_internal_headers(monkeypatch):
    monkeypatch.setenv("RTB_API_TOKEN", "infra-secret")
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "r1", "seatbid": [{"bid": [{"id": "b1", "impid": "1", "price": 1.5}]}]})

    call = ssp_check.go_bidder_caller(_client(handler))
    req = ssp_check.make_test_bid_request("banner", request_id="r1", bid_floor=0.5)
    resp = _run(call("key1", req, verify_token=False))

    assert resp["seatbid"][0]["bid"][0]["price"] == 1.5
    assert seen["url"].endswith("/rtb/bid/key1")
    # internal-токен -> Go обходит IP-whitelist; Skip-Token -> не сверяет SSP-токен
    assert seen["headers"]["x-centraldsp-internal-check"] == "infra-secret"
    assert seen["headers"]["x-centraldsp-skip-token"] == "1"
    assert "x-centraldsp-token" not in seen["headers"]
    # test=1: Go не резервирует бюджет клиента
    assert seen["body"]["test"] == 1


def test_204_maps_to_none(monkeypatch):
    monkeypatch.setenv("RTB_API_TOKEN", "s")
    call = ssp_check.go_bidder_caller(_client(lambda r: httpx.Response(204)))
    assert _run(call("key1", {"id": "x", "imp": []}, verify_token=False)) is None


def test_explicit_bad_token_is_forwarded_without_skip(monkeypatch):
    """Негативный auth-тест: неверный токен уходит в Go как есть, Skip-Token НЕ ставится."""
    monkeypatch.setenv("RTB_API_TOKEN", "s")
    seen = {}

    def handler(req):
        seen["headers"] = dict(req.headers)
        return httpx.Response(403, text="invalid ssp token")

    call = ssp_check.go_bidder_caller(_client(handler))
    with pytest.raises(HTTPException) as ei:
        _run(call("key1", {"id": "x", "imp": []}, ssp_token="invalid-token-check"))
    assert ei.value.status_code == 403
    assert seen["headers"]["x-centraldsp-token"] == "invalid-token-check"
    assert "x-centraldsp-skip-token" not in seen["headers"]


def test_404_unknown_endpoint(monkeypatch):
    monkeypatch.setenv("RTB_API_TOKEN", "s")
    call = ssp_check.go_bidder_caller(_client(lambda r: httpx.Response(404, text="unknown endpoint")))
    with pytest.raises(HTTPException) as ei:
        _run(call("nope", {"id": "x", "imp": []}))
    assert ei.value.status_code == 404


def test_connection_error_maps_to_503(monkeypatch):
    monkeypatch.setenv("RTB_API_TOKEN", "s")

    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    call = ssp_check.go_bidder_caller(_client(handler))
    with pytest.raises(HTTPException) as ei:
        _run(call("key1", {"id": "x", "imp": []}, verify_token=False))
    assert ei.value.status_code == 503
    assert "Go-биддер недоступен" in ei.value.detail


def test_no_internal_token_means_no_skip_header(monkeypatch):
    """Без RTB_API_TOKEN обходов нет: Go сверит токен и IP как для обычного SSP."""
    monkeypatch.delenv("RTB_API_TOKEN", raising=False)
    seen = {}

    def handler(req):
        seen["headers"] = dict(req.headers)
        return httpx.Response(204)

    call = ssp_check.go_bidder_caller(_client(handler))
    _run(call("key1", {"id": "x", "imp": []}, verify_token=False))
    assert "x-centraldsp-internal-check" not in seen["headers"]
    assert "x-centraldsp-skip-token" not in seen["headers"]


def test_run_connection_check_end_to_end_through_adapter(monkeypatch):
    """Полный чек-лист через адаптер: формат OK, неверный токен 403, неизвестный ключ 404."""
    monkeypatch.setenv("RTB_API_TOKEN", "s")

    def handler(req: httpx.Request):
        key = req.url.path.rsplit("/", 1)[-1]
        if key != "key1":
            return httpx.Response(404, text="unknown endpoint")
        if req.headers.get("x-centraldsp-token") == "invalid-token-check":
            return httpx.Response(403, text="invalid ssp token")
        body = json.loads(req.content)
        return httpx.Response(200, json={
            "id": body["id"], "cur": "RUB",
            "seatbid": [{"bid": [{"id": "b", "impid": "1", "price": 2.0, "adm": "<div/>", "crid": "c1",
                                  "nurl": "http://x/win", "adomain": ["a.ru"]}]}],
        })

    partner = {"endpoint_key": "key1", "bid_floor": 0.5, "allowed_ad_types": ["banner"],
               "tmax_ms": 5000, "auth_token_hash": "abc"}
    result = _run(ssp_check.run_connection_check(partner, ssp_check.go_bidder_caller(_client(handler))))
    by_key = {c["key"]: c for c in result["checks"]}
    assert by_key["bid_banner"]["ok"], by_key["bid_banner"]
    assert by_key["auth"]["ok"]
    assert by_key["unknown_endpoint"]["ok"]
    assert result["passed"] is True


def test_request_feed_reload(monkeypatch):
    monkeypatch.setenv("RTB_API_TOKEN", "s")
    seen = {}

    def handler(req):
        seen["path"] = req.url.path
        seen["tok"] = req.headers.get("x-centraldsp-internal-check")
        return httpx.Response(200, json={"ok": True})

    assert _run(ssp_check.request_feed_reload(_client(handler))) is True
    assert seen == {"path": "/internal/feed/reload", "tok": "s"}

    monkeypatch.delenv("RTB_API_TOKEN")
    assert _run(ssp_check.request_feed_reload(_client(handler))) is False
