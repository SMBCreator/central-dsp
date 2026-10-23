"""GEO v2 (docs/GEO.md): нормализация, сопоставление и матчинг.

Кейсы общие с Go (bidder-go/internal/geo/testdata/fixtures.json) — расхождение
Python-эталона и биддера валит оба набора тестов.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import geo_v2
from geo_norm import geo_normalize
from routers.rtb import _match_targeting
from targeting_contract import validate_targeting

FIXTURES = json.loads(
    (Path(__file__).resolve().parents[2] / "bidder-go" / "internal" / "geo" / "testdata" / "fixtures.json")
    .read_text(encoding="utf-8")
)
INDEX = geo_v2.GeoIndex(FIXTURES["places"], FIXTURES["aliases"], "fixtures")


@pytest.mark.parametrize("raw, want", FIXTURES["normalize"])
def test_normalize(raw, want):
    assert geo_normalize(raw) == want


@pytest.mark.parametrize("case", FIXTURES["resolve"], ids=lambda c: c["name"])
def test_resolve(case):
    got = INDEX.resolve(
        case.get("partner", ""), case.get("country"), case.get("region"), case.get("city"),
        case.get("lat"), case.get("lon"), use_latlon=case.get("latlon", False),
    )
    assert list(got.chain) == case["want_chain"]
    assert got.level == case["want_level"]
    assert got.unmatched_region == case.get("want_unmatched_region", "")
    assert got.unmatched_city == case.get("want_unmatched_city", "")


@pytest.mark.parametrize("case", FIXTURES["match"], ids=lambda c: c["name"])
def test_match(case):
    resolved = geo_v2.Resolved(tuple(case["chain"]), case["level"])
    assert geo_v2.match_geo_v2(case["targeting"], resolved, INDEX) is case["want"]


# --- интеграция в _match_targeting (Python hot path) -------------------------


def _req(country="RUS", region="", city=""):
    return {"device": {"geo": {"country": country, "region": region, "city": city}}}


SOCHI = INDEX.resolve("", "RUS", "", "Сочи")


def test_flag_off_v2_targeting_is_fail_closed(monkeypatch):
    monkeypatch.delenv("GEO_V2_ENABLED", raising=False)
    assert _match_targeting({"geo_place_ids": [542415]}, _req(city="Сочи"), geo_v2_resolved=SOCHI) is False
    assert _match_targeting({"geo_exclude_place_ids": [524901]}, _req(city="Сочи"), geo_v2_resolved=SOCHI) is False


def test_flag_on_v2_targeting_matches(monkeypatch):
    monkeypatch.setenv("GEO_V2_ENABLED", "1")
    assert _match_targeting({"geo_place_ids": [542415]}, _req(city="Сочи"), geo_v2_resolved=SOCHI) is True
    assert _match_targeting({"geo_place_ids": [551487]}, _req(city="Сочи"), geo_v2_resolved=SOCHI) is False
    # Без сопоставленного гео (None) — unknown: strict по умолчанию.
    assert _match_targeting({"geo_place_ids": [542415]}, _req(city="Сочи")) is False


@pytest.mark.parametrize("flag", ["", "1"])
def test_legacy_targeting_unchanged_by_flag(monkeypatch, flag):
    """Regression: старые ключи дают тот же результат при любом значении флага."""
    monkeypatch.setenv("GEO_V2_ENABLED", flag)
    cases = [
        ({"geo_countries": ["RU"]}, _req(), True),
        ({"geo_countries": ["KZ"]}, _req(), False),
        ({"geo_regions": ["Москва"]}, _req(region="москва"), True),
        ({"geo_regions": ["Москва"]}, _req(region="Moscow"), False),
        ({"geo_cities": ["Сочи"], "geo_regions": ["Краснодарский край"]}, _req(region="Краснодарский край", city="Сочи"), True),
        ({"geo_cities": ["Сочи"]}, _req(country="", city=""), False),
        ({}, _req(country=""), True),
    ]
    for targeting, req, want in cases:
        assert _match_targeting(targeting, req, geo_v2_resolved=SOCHI) is want, targeting


def test_legacy_and_v2_are_and_ed(monkeypatch):
    monkeypatch.setenv("GEO_V2_ENABLED", "1")
    t = {"geo_countries": ["KZ"], "geo_place_ids": [542415]}
    assert _match_targeting(t, _req(city="Сочи"), geo_v2_resolved=SOCHI) is False


# --- контракт ------------------------------------------------------------------


def test_contract_accepts_v2_keys():
    t = validate_targeting({"geo_place_ids": [542415], "geo_exclude_place_ids": [491422], "geo_unknown": "lenient"})
    assert t["geo_place_ids"] == [542415]


@pytest.mark.parametrize(
    "bad, fragment",
    [
        ({"geo_place_ids": [0]}, "диапазоне"),
        ({"geo_place_ids": ["524901"]}, "список целых"),
        ({"geo_place_ids": [1, 1]}, "повторяются"),
        ({"geo_place_ids": list(range(1, 502))}, "не больше 500"),
        ({"geo_place_ids": [1], "geo_exclude_place_ids": [1]}, "и в исключениях"),
        ({"geo_unknown": "maybe"}, "strict, lenient"),
    ],
)
def test_contract_rejects_bad_v2(bad, fragment):
    with pytest.raises(ValueError) as ei:
        validate_targeting(bad)
    assert fragment in str(ei.value)
