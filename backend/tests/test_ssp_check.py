"""Тесты автопроверки подключения SSP (ssp_check): чистые функции."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ssp_check import ip_allowed, make_test_bid_request, validate_bid_response  # noqa: E402


# --- ip_allowed ---------------------------------------------------------------


def test_empty_whitelist_allows_all():
    assert ip_allowed("203.0.113.7", []) is True
    assert ip_allowed(None, []) is True


def test_exact_ip_match():
    assert ip_allowed("203.0.113.7", ["203.0.113.7"]) is True
    assert ip_allowed("203.0.113.8", ["203.0.113.7"]) is False


def test_cidr_match():
    assert ip_allowed("10.1.2.3", ["10.1.0.0/16"]) is True
    assert ip_allowed("10.2.0.1", ["10.1.0.0/16"]) is False
    assert ip_allowed("2001:db8::1", ["2001:db8::/32"]) is True


def test_nonempty_whitelist_requires_ip():
    assert ip_allowed(None, ["203.0.113.7"]) is False
    assert ip_allowed("not-an-ip", ["203.0.113.7"]) is False


def test_garbage_entries_ignored():
    assert ip_allowed("203.0.113.7", ["garbage", "", "203.0.113.7"]) is True
    assert ip_allowed("203.0.113.8", ["garbage", ""]) is False


# --- validate_bid_response ------------------------------------------------------


def _valid_response(price: float = 2.5) -> dict:
    return {
        "id": "req-1",
        "seatbid": [
            {
                "seat": "centraldsp",
                "bid": [
                    {
                        "id": "bid-1",
                        "impid": "1",
                        "price": price,
                        "adm": "<div>ad</div>",
                        "crid": "cr-1",
                        "nurl": "https://dsp.example/rtb/win?bid=x",
                    }
                ],
            }
        ],
    }


def test_valid_response_passes():
    assert validate_bid_response(_valid_response()) == []


def test_none_response_is_reported():
    errors = validate_bid_response(None)
    assert len(errors) == 1 and "no-bid" in errors[0].lower()


def test_missing_fields_detected():
    resp = _valid_response()
    del resp["seatbid"][0]["bid"][0]["adm"]
    del resp["seatbid"][0]["bid"][0]["nurl"]
    errors = validate_bid_response(resp)
    assert any("adm" in e for e in errors)
    assert any("nurl" in e for e in errors)


def test_price_below_floor_detected():
    errors = validate_bid_response(_valid_response(price=0.5), bid_floor=1.0)
    assert any("ниже bid floor" in e for e in errors)


def test_zero_price_detected():
    errors = validate_bid_response(_valid_response(price=0))
    assert any("price" in e for e in errors)


def test_missing_seatbid_detected():
    errors = validate_bid_response({"id": "req-1"})
    assert any("seatbid" in e for e in errors)


# --- make_test_bid_request ------------------------------------------------------


def test_banner_request_shape():
    req = make_test_bid_request("banner", request_id="r1", bid_floor=0.3)
    assert req["id"] == "r1"
    assert req["imp"][0]["banner"]["w"] == 300
    assert req["imp"][0]["bidfloor"] == 0.3
    assert req["test"] == 1
    assert req["site"]["domain"].endswith(".internal")


def test_native_request_has_native_spec():
    req = make_test_bid_request("native", request_id="r2")
    assert "native" in req["imp"][0]
    assert '"ver":"1.2"' in req["imp"][0]["native"]["request"]


def test_video_request_has_video_block():
    req = make_test_bid_request("video", request_id="r3")
    assert req["imp"][0]["video"]["mimes"] == ["video/mp4"]
