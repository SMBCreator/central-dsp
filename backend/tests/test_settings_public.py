"""GET /settings/public — публичный URL Go-биддера для SSP (audit R-01).

Раньше UI склеивал `${window.location.origin}/api/rtb/bid/{key}` — адрес
Python-биддера, который в production выключен. Теперь адрес приходит с
backend'а из PUBLIC_BIDDER_BASE_URL (fallback PUBLIC_BASE_URL), а если он не
настроен — UI видит null и показывает предупреждение вместо выдуманного URL.
"""

from fastapi.testclient import TestClient

import main
from auth import current_user
from routers.settings import public_bidder_base_url
from startup_checks import collect_warnings


def test_public_bidder_base_url_prefers_dedicated_var():
    env = {"PUBLIC_BIDDER_BASE_URL": "https://bidder.example.com/", "PUBLIC_BASE_URL": "https://dsp.example.com"}
    assert public_bidder_base_url(env) == "https://bidder.example.com"


def test_public_bidder_base_url_falls_back_to_public_base_url():
    assert public_bidder_base_url({"PUBLIC_BASE_URL": "https://dsp.example.com/"}) == "https://dsp.example.com"


def test_public_bidder_base_url_none_when_unset():
    assert public_bidder_base_url({}) is None
    assert public_bidder_base_url({"PUBLIC_BIDDER_BASE_URL": "  "}) is None


def test_startup_warns_when_bidder_url_missing():
    assert any("PUBLIC_BIDDER_BASE_URL" in w for w in collect_warnings({}))
    assert not any("PUBLIC_BIDDER_BASE_URL" in w for w in collect_warnings({"PUBLIC_BASE_URL": "https://x"}))


def test_endpoint_returns_template(monkeypatch):
    monkeypatch.setenv("PUBLIC_BIDDER_BASE_URL", "https://bidder.example.com")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    main.app.dependency_overrides[current_user] = lambda: {"id": "u1", "role": "viewer"}
    try:
        with TestClient(main.app) as c:
            r = c.get("/settings/public")
    finally:
        main.app.dependency_overrides.pop(current_user, None)
    assert r.status_code == 200
    body = r.json()
    assert body["bidder_base_url"] == "https://bidder.example.com"
    assert body["rtb_bid_url_template"] == "https://bidder.example.com/rtb/bid/{endpoint_key}"
    assert body["ssp_token_header"] == "X-CentralDSP-Token"


def test_endpoint_requires_auth():
    with TestClient(main.app) as c:
        assert c.get("/settings/public").status_code == 401


def test_endpoint_null_when_unset(monkeypatch):
    monkeypatch.delenv("PUBLIC_BIDDER_BASE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    main.app.dependency_overrides[current_user] = lambda: {"id": "u1", "role": "viewer"}
    try:
        with TestClient(main.app) as c:
            r = c.get("/settings/public")
    finally:
        main.app.dependency_overrides.pop(current_user, None)
    assert r.status_code == 200
    assert r.json()["bidder_base_url"] is None
    assert r.json()["rtb_bid_url_template"] is None
