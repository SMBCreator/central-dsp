"""Тесты throttling-предфильтра (Часть C).

Инварианты:
  - fail-open: ошибки/пустой кэш/выключенный фильтр никогда не отсекают запрос;
  - причины отсечения — только из bounded enum (те же строки, что bidder_prefilter_drops_total в Go);
  - легитимный RU-трафик с device id проходит все проверки.
"""
import pytest

import prefilter


@pytest.fixture(autouse=True)
def _reset():
    prefilter._reset_dc_cache_for_tests()
    yield
    prefilter._reset_dc_cache_for_tests()


def _req(ua="Mozilla/5.0 (Android 14)", country="RUS", ifa="abc-123", ip="93.184.216.34"):
    device = {}
    if ua is not None:
        device["ua"] = ua
    if country is not None:
        device["geo"] = {"country": country}
    if ifa is not None:
        device["ifa"] = ifa
    if ip is not None:
        device["ip"] = ip
    return {"id": "req-1", "imp": [{"id": "1"}], "device": device}


# ------------------------------------------------------------------ passes ---

def test_legit_request_passes():
    assert prefilter.check(_req()) is None


def test_empty_ua_passes():
    """Пустой UA легитимен для in-app инвентаря — не блокируем."""
    assert prefilter.check(_req(ua="")) is None


def test_missing_country_passes():
    """Гео-фильтр срабатывает только на ЯВНО указанную чужую страну."""
    assert prefilter.check(_req(country=None)) is None


def test_no_device_block_but_has_ip_passes():
    assert prefilter.check(_req(ifa=None)) is None  # ip остаётся


def test_malformed_request_fail_open():
    """Мусорные структуры не роняют hot path (исключение -> fail-open)."""
    assert prefilter.check({"device": "not-a-dict"}) is None
    # Пустой запрос без device вовсе — легитимно отсекается как no_device_id
    assert prefilter.check({}) == "no_device_id"


# ------------------------------------------------------------------- drops ---

@pytest.mark.parametrize("ua", [
    "Googlebot/2.1 (+http://www.google.com/bot.html)",
    "python-requests/2.31.0",
    "curl/8.4.0",
    "Mozilla/5.0 HeadlessChrome/119.0",
    "okhttp/4.12.0",
])
def test_bot_ua_dropped(ua):
    assert prefilter.check(_req(ua=ua)) == "bot_ua"


@pytest.mark.parametrize("country", ["USA", "DE", "cn"])
def test_foreign_geo_dropped(country):
    assert prefilter.check(_req(country=country)) == "geo_unserved"


@pytest.mark.parametrize("country", ["RU", "ru", "RUS", "rus"])
def test_ru_geo_passes(country):
    assert prefilter.check(_req(country=country)) is None


def test_no_device_id_dropped():
    assert prefilter.check(_req(ifa=None, ip=None)) == "no_device_id"


# ------------------------------------------------------- datacenter subnets ---

def test_dc_ip_dropped_with_warm_cache(monkeypatch):
    import time as _t
    prefilter._dc_cache = (frozenset({"45.155.204."}), _t.monotonic() + 60)
    assert prefilter.check(_req(ip="45.155.204.77")) == "datacenter_ip"


def test_dc_p16_prefix(monkeypatch):
    import time as _t
    prefilter._dc_cache = (frozenset({"45.155."}), _t.monotonic() + 60)
    assert prefilter.check(_req(ip="45.155.1.2")) == "datacenter_ip"


def test_dc_cold_cache_fail_open():
    """Холодный кэш (Redis ещё не опрошен) — запрос пропускается."""
    assert prefilter.check(_req(ip="45.155.204.77")) is None


def test_ipv6_not_matched_by_dc_list():
    import time as _t
    prefilter._dc_cache = (frozenset({"45.155."}), _t.monotonic() + 60)
    req = _req(ip=None)
    req["device"]["ipv6"] = "2001:db8::1"
    assert prefilter.check(req) is None


# -------------------------------------------------------------- cidr parse ---

@pytest.mark.parametrize("cidr,expected", [
    ("45.155.204.0/24", "45.155.204."),
    ("45.155.0.0/16", "45.155."),
    (" 10.0.0.0/24 ", "10.0.0."),
])
def test_cidr_to_prefix_valid(cidr, expected):
    assert prefilter.cidr_to_prefix(cidr) == expected


@pytest.mark.parametrize("cidr", [
    "45.155.204.0/8",       # неподдерживаемая маска
    "45.155.204.0",          # нет маски
    "999.1.1.0/24",          # октет > 255
    "abc/24",
    "",
])
def test_cidr_to_prefix_invalid(cidr):
    assert prefilter.cidr_to_prefix(cidr) is None
