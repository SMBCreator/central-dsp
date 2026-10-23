"""B13e: чистые функции ОРС-модели актов — без БД.

* net_of / split_amount / amount_diff_pct;
* invoice_body: contracts[] по изначальным договорам, contractorRole=ORS;
* invoice_link_body / invoice_unlink_all_body: по одному items[] на договор.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ord.invoices import (
    InvoiceError,
    amount_diff_pct,
    net_of,
    parse_vat_rate,
    split_amount,
)
from ord.ozon_mapping import invoice_body, invoice_link_body, invoice_unlink_all_body


def test_net_of_and_vat_rate():
    assert net_of("122.00", True, 22) == Decimal("100.00")
    assert net_of("122.00", False, 22) == Decimal("122.00")
    assert net_of("122.00", True, 0) == Decimal("122.00")
    assert parse_vat_rate(None, True) == 22 and parse_vat_rate(None, False) == 0
    assert parse_vat_rate("20", True) == 20
    with pytest.raises(InvoiceError):
        parse_vat_rate(101, True)
    with pytest.raises(InvoiceError):
        parse_vat_rate("abc", True)


def test_split_amount_is_exact_to_the_kopeck():
    w = {"a": Decimal(1), "b": Decimal(1), "c": Decimal(1)}
    s = split_amount(Decimal("100.00"), w)
    assert sum(s.values()) == Decimal("100.00") and s == {"a": Decimal("33.33"), "b": Decimal("33.33"), "c": Decimal("33.34")}
    assert split_amount(Decimal("10.00"), {"a": Decimal(0), "b": Decimal(0)}) == {"a": Decimal("5.00"), "b": Decimal("5.00")}
    assert split_amount(Decimal("10.00"), {}) == {}


def test_amount_diff_pct():
    assert amount_diff_pct("101", "100") == pytest.approx(1.0)
    assert amount_diff_pct("0", "0") is None
    assert amount_diff_pct("5", "0") == 100.0


def test_invoice_body_ors_contracts():
    body = invoice_body({
        "external_id": "inv-1", "contract_external_id": "CT-E1", "number": "А-1",
        "invoice_date": "2026-02-05", "period_start": "2026-01-01", "period_end": "2026-01-31",
        "amount_gross": Decimal("50.00"), "is_vat": True, "client_role": "agency", "contractor_role": "ors",
        "contracts": [{"contract_external_id": "CT-I1", "amount_gross": Decimal("35.00"), "is_vat": True},
                      {"contract_external_id": "CT-I2", "amount_gross": Decimal("15.00"), "is_vat": True}],
    })
    assert body["externalContractId"] == "CT-E1"
    assert body["servicePrice"] == {"amount": "50.00", "excludingAmount": "40.98", "vatRate": "22"}
    assert body["clientRole"] == "ORGANISATION_ROLE_RA" and body["contractorRole"] == "ORGANISATION_ROLE_ORS"
    assert body["contracts"] == [
        {"externalContractId": "CT-I1", "price": {"amount": "35.00", "excludingAmount": "28.69", "vatRate": "22"}},
        {"externalContractId": "CT-I2", "price": {"amount": "15.00", "excludingAmount": "12.30", "vatRate": "22"}},
    ]
    # акт B13b без contracts — один договор акта, старое поле amount
    legacy = invoice_body({
        "external_id": "inv-0", "contract_external_id": "CT-1", "number": "А-0", "invoice_date": "2026-02-05",
        "period_start": "2026-01-01", "period_end": "2026-01-31", "amount": 10, "is_vat": False,
    })
    no_vat = {"amount": "10.00", "excludingAmount": "10.00", "vatRate": "100"}  # 100 = без НДС
    assert legacy["contracts"] == [{"externalContractId": "CT-1", "price": no_vat}]
    assert legacy["servicePrice"] == no_vat


def test_invoice_link_and_unlink_bodies_per_contract():
    data = {"external_invoice_id": "inv-1", "contract_external_id": "CT-E1",
            "groups": [{"contract_external_id": "CT-I1", "external_creative_ids": ["c1:v1"]},
                       {"contract_external_id": "CT-I2", "external_creative_ids": []}]}
    link = invoice_link_body(data)
    assert link == {"items": [{"externalInvoiceId": "inv-1", "externalContractId": "CT-I1", "externalCreativeIds": ["c1:v1"]}]}
    unlink = invoice_unlink_all_body(data)
    assert [i["externalContractId"] for i in unlink["items"]] == ["CT-E1", "CT-I1", "CT-I2"]
    assert all(i["deleteAllCreatives"] is True for i in unlink["items"])
    # без groups — как в B13b
    legacy = invoice_link_body({"external_invoice_id": "i", "contract_external_id": "CT-1", "external_creative_ids": ["a:v1"]})
    assert legacy["items"][0]["externalContractId"] == "CT-1"
