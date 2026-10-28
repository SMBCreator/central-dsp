"""C30: реквизиты иностранных контрагентов — маппинг в ОРД v6 и валидация API (без сети и БД)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ord.adapter import OrdDataError
from ord.ozon_mapping import organisation_body
from ord.platform import org_payload
from routers.ord_router import CounterpartyIn

FOREIGN = {"external_id": "cp-f", "name": "Foreign LLP", "inn": "123456789012", "legal_type": "foreign",
           "region_number": "398", "tax_id": "123456789012", "registration_number": "R-77"}


def test_foreign_body_has_requisites():
    body = organisation_body(FOREIGN)
    assert body["legalType"] == "LEGAL_TYPE_FOREIGN_LEGAL"
    assert body["regionNumber"] == "398" and body["taxId"] == "123456789012" and body["registrationNumber"] == "R-77"


def test_foreign_body_one_number_is_enough():
    body = organisation_body({**FOREIGN, "tax_id": None})
    assert "taxId" not in body and body["registrationNumber"] == "R-77"


@pytest.mark.parametrize("patch,code", [
    ({"region_number": None}, "foreign_region_missing"),
    ({"region_number": "39"}, "foreign_region_missing"),
    ({"region_number": "643"}, "foreign_region_russia"),
    ({"tax_id": " ", "registration_number": None}, "foreign_ids_missing"),
])
def test_foreign_body_rejects_incomplete(patch, code):
    with pytest.raises(OrdDataError, match=code):
        organisation_body({**FOREIGN, **patch})


def test_resident_body_unchanged_even_with_stray_fields():
    body = organisation_body({**FOREIGN, "legal_type": "ul"})
    assert not {"regionNumber", "taxId", "registrationNumber"} & body.keys()


def test_org_payload_carries_requisites_from_row():
    row = {"id": "cp-f", "name": "Foreign LLP", "inn": "1", "legal_type": "foreign", "role": "advertiser",
           "region_number": "398", "tax_id": "T", "registration_number": None}
    data = org_payload(row)
    assert (data["region_number"], data["tax_id"], data["registration_number"]) == ("398", "T", None)
    assert organisation_body(data)["taxId"] == "T"


def test_api_model_validates_foreign():
    ok = CounterpartyIn(name="F", inn="123456789012", legal_type="foreign", region_number="398", tax_id=" T1 ")
    assert ok.tax_id == "T1" and ok.registration_number is None
    with pytest.raises(ValidationError, match="ОКСМ"):
        CounterpartyIn(name="F", inn="123456789012", legal_type="foreign", tax_id="T1")
    with pytest.raises(ValidationError, match="налоговый или регистрационный"):
        CounterpartyIn(name="F", inn="123456789012", legal_type="foreign", region_number="398")
    with pytest.raises(ValidationError):
        CounterpartyIn(name="F", inn="123456789012", legal_type="foreign", region_number="RUS", tax_id="T")


def test_api_model_drops_requisites_for_resident():
    cp = CounterpartyIn(name="ООО", inn="7700000001", legal_type="ul", region_number="398", tax_id="T")
    assert cp.region_number is None and cp.tax_id is None and cp.registration_number is None
