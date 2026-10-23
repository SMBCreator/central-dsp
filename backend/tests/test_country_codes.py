"""F-B1: device.geo.country (OpenRTB alpha-3) vs targeting.geo_countries (UI alpha-2)."""

import pytest

from country_codes import ALPHA3_TO_ALPHA2, normalize_country
from routers.rtb import _match_targeting


@pytest.mark.parametrize(
    "raw,want",
    [("RUS", "RU"), ("rus", "RU"), (" RUS ", "RU"), ("RU", "RU"), ("ru", "RU"),
     ("KAZ", "KZ"), ("XKX", "XK"), ("ZZZ", "ZZZ"), ("", ""), (None, "")],
)
def test_normalize_country(raw, want):
    assert normalize_country(raw) == want


def test_table_shape():
    assert len(ALPHA3_TO_ALPHA2) >= 249
    assert all(len(k) == 3 and len(v) == 2 for k, v in ALPHA3_TO_ALPHA2.items())


@pytest.mark.parametrize(
    "targets,req_country,want",
    [
        (["RU"], "RUS", True),
        (["RU"], "RU", True),
        (["RUS"], "RUS", True),
        (["ru"], "rus", True),
        (["RU"], "KAZ", False),
        (["KZ", "RU"], "RUS", True),
        (["RU"], None, False),
    ],
)
def test_match_targeting_country(targets, req_country, want):
    req = {"device": {"geo": {"country": req_country}}} if req_country is not None else {"device": {}}
    assert _match_targeting({"geo_countries": targets}, req) is want
