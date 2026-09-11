"""B13 (L-02/L-03): маппинг в API ОРД Ozon и построчный результат статистики.

Без сети и БД: тела запросов проверяются как чистые функции, клиент — через
httpx.MockTransport. Спека: docs/audit/ord_spec.md.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ord import ozon as ozon_mod
from ord.adapter import OrdDataError, OrdError
from ord.ozon import OzonOrdClient
from ord.invoices import month_slices, unit_cost
from ord.ozon_mapping import (
    contract_body,
    creative_body,
    creative_erid,
    invoice_body,
    invoice_link_body,
    invoice_unlink_all_body,
    organisation_body,
    period_bounds,
    statistic_body,
)
from ord.service import external_creative_id, external_statistic_id


def test_external_ids_are_versioned():
    assert external_creative_id("abc", 2) == "abc:v2"
    assert external_statistic_id("abc", 2, "2026-08") == "abc:v2:2026-08"


def test_period_bounds_last_day():
    assert period_bounds("2026-02") == ("2026-02-01", "2026-02-28")
    assert period_bounds("2028-02") == ("2028-02-01", "2028-02-29")
    assert period_bounds("2026-12") == ("2026-12-01", "2026-12-31")


def test_organisation_body_maps_legal_type_and_ogrn():
    body = organisation_body({"external_id": "cp-1", "name": "ООО Тест", "inn": "7700000001",
                              "legal_type": "ul", "ogrn": "1027700000001", "address": "Москва"})
    assert body["externalOrganisationId"] == "cp-1"
    assert body["legalType"] == "LEGAL_TYPE_LEGAL"
    assert body["ogrn"] == "1027700000001" and "ogrnip" not in body
    ip = organisation_body({"external_id": "cp-2", "name": "ИП Иванов", "inn": "770000000001",
                            "legal_type": "ip", "ogrn": "3"})
    assert ip["legalType"] == "LEGAL_TYPE_ENTREPRENEUR" and ip["ogrnip"] == "3"


def test_contract_body_requires_party_external_ids_and_parent():
    body = contract_body({
        "external_id": "ct-1", "client_external_id": "cp-rd", "contractor_external_id": "cp-ors",
        "parent_external_id": "ct-0", "number": "Д-1", "signed_at": "2026-01-01",
        "contract_type": "mediation", "subject_type": "distribution", "amount": "1000", "is_vat": True,
    })
    assert body["externalOrganisationCustomerId"] == "cp-rd"
    assert body["externalOrganisationPerformerId"] == "cp-ors"
    assert body["externalParentId"] == "ct-0"
    assert body["contractType"] == "CONTRACT_TYPE_INTERMEDIARY"
    assert body["price"] == "1000" and body["withNds"] is True
    assert body["isCreativeReporter"] is True
    with pytest.raises(KeyError):
        contract_body({"external_id": "ct-1", "number": "x", "signed_at": "2026-01-01"})


def test_creative_body_is_per_version_and_carries_kktu_media_target():
    payload = {
        "external_creative_id": "cr-1:v2", "contract_external_id": "ct-1", "type": "banner",
        "name": "Баннер", "click_url": "https://x/land", "description": "d",
        "media_urls": ["https://cdn/a.png"], "texts": ["Купи"], "kktu": "30.10",
    }
    body = creative_body(payload)
    assert body["externalCreativeId"] == "cr-1:v2"
    assert body["externalContractIds"] == ["ct-1"]
    assert body["okvedCodes"] == ["30.10"]
    assert body["advObjectType"] == "ADV_OBJECT_TYPE_BANNER"
    assert body["paymentType"] == "PAYMENT_TYPE_CPM"
    assert body["targetLink"] == "https://x/land"
    assert body["mediaData"][0]["file"]["url"] == "https://cdn/a.png"
    assert {"text": "Купи"} in body["mediaData"]
    assert body["isNative"] is False
    native = creative_body({**payload, "type": "native"})
    assert native["isNative"] is True and native["advObjectType"] == "ADV_OBJECT_TYPE_TEXT_GRAPHIC_BLOCK"


def test_creative_erid_from_marker_or_top_level():
    assert creative_erid({"creative": {"marker": "2VfnxyZ"}}) == "2VfnxyZ"
    assert creative_erid({"erid": "abc"}) == "abc"
    assert creative_erid({"creative": {}}) is None


def test_statistic_body_unit_cost_per_1000_and_upsert_id():
    row = {"external_statistic_id": "cr:v1:2026-07", "external_creative_id": "cr:v1",
           "impressions": 2000, "clicks": 5, "spend": "30.00"}
    body = statistic_body("2026-07-01", "2026-07-31", row, "central-dsp")
    assert body["externalStatisticId"] == "cr:v1:2026-07"
    assert body["externalCreativeId"] == "cr:v1"
    assert body["externalPlatformId"] == "central-dsp"
    assert body["viewsCountByFact"] == "2000" and body["viewsCountByInvoice"] == "2000"
    assert body["moneySpent"] == "30.00"
    assert body["unitCost"] == "15.0000"  # 30 руб / 2000 показов * 1000
    assert body["dateStartFact"] == "2026-07-01" and body["dateEndFact"] == "2026-07-31"


# --- клиент: реальные пути и построчный результат ------------------------------
def _client_with(handler) -> OzonOrdClient:
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    class _Patched(real_async_client):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    ozon_mod.httpx.AsyncClient = _Patched  # type: ignore[attr-defined]
    return OzonOrdClient(api_key="k", base_url="https://api-ord.test")


@pytest.fixture(autouse=True)
def _restore_httpx():
    orig = ozon_mod.httpx.AsyncClient
    yield
    ozon_mod.httpx.AsyncClient = orig


def test_ozon_register_creative_uses_real_path_and_marker():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers.get("Authorization")
        return httpx.Response(200, json={"creative": {"marker": "2VtzqvERID", "externalCreativeId": "cr:v1"}})

    c = _client_with(handler)
    out = asyncio.run(c.register_creative({
        "external_creative_id": "cr:v1", "contract_external_id": "ct", "type": "banner", "name": "n",
        "kktu": "30.10", "media_urls": [], "texts": [],
    }))
    assert seen["path"] == "/api/external/creative"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["externalCreativeId"] == "cr:v1"
    assert out == {"external_id": "cr:v1", "erid": "2VtzqvERID"}


def test_ozon_submit_stats_isolates_rejected_row(monkeypatch):
    monkeypatch.setenv("ORD_PLATFORM_EXTERNAL_ID", "dsp-1")
    calls: list[list[str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        ids = [s["externalStatisticId"] for s in body["statistics"]]
        calls.append(ids)
        assert req.url.path == "/api/external/v2/statistic"
        assert all(s["externalPlatformId"] == "dsp-1" for s in body["statistics"])
        if "bad:v1:2026-07" in ids:
            return httpx.Response(400, json={"error": "creative not found"})
        return httpx.Response(200, json={"message": "ok"})

    c = _client_with(handler)
    rows = [
        {"external_statistic_id": f"{n}:v1:2026-07", "external_creative_id": f"{n}:v1", "impressions": 10, "spend": "1"}
        for n in ("a", "bad", "c")
    ]
    out = asyncio.run(c.submit_stats("2026-07", rows))
    assert out["accepted"] == ["a:v1:2026-07", "c:v1:2026-07"]
    assert [r["id"] for r in out["rejected"]] == ["bad:v1:2026-07"]
    assert "creative not found" in out["rejected"][0]["error"]
    # первый вызов — батч из трёх, затем по одной строке
    assert calls[0] == ["a:v1:2026-07", "bad:v1:2026-07", "c:v1:2026-07"]
    assert calls[1:] == [["a:v1:2026-07"], ["bad:v1:2026-07"], ["c:v1:2026-07"]]


def test_ozon_submit_stats_5xx_fails_whole_export(monkeypatch):
    monkeypatch.setattr(ozon_mod, "_RETRIES", 1)  # без экспоненциальных пауз

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="maintenance")

    c = _client_with(handler)
    rows = [{"external_statistic_id": "a:v1:2026-07", "external_creative_id": "a:v1", "impressions": 1, "spend": "1"}]
    with pytest.raises(OrdError) as ei:
        asyncio.run(c.submit_stats("2026-07", rows))
    assert not isinstance(ei.value, OrdDataError)


# --- акты (B13b) ---------------------------------------------------------------
def test_statistic_body_invoice_fields_override_fact_only_where_intended():
    """Строка акта: факт остаётся в viewsCountByFact, значения акта — в
    viewsCountByInvoice/moneySpent/unitCost, даты — срез месяца ∩ период акта."""
    row = {"external_statistic_id": "cr:v1:2026-09", "external_creative_id": "cr:v1",
           "impressions": 2000, "clicks": 5, "spend": "30.00",
           "invoice_impressions": 1900, "invoice_spend": "36.00", "unit_cost": "18.9474",
           "date_start": "2026-09-15", "date_end": "2026-09-30"}
    body = statistic_body("2026-09-01", "2026-09-30", row, "central-dsp")
    assert body["viewsCountByFact"] == "2000" and body["viewsCountByInvoice"] == "1900"
    assert body["moneySpent"] == "36.00" and body["unitCost"] == "18.9474"
    assert body["dateStartFact"] == "2026-09-15" and body["dateEndFact"] == "2026-09-30"
    assert body["dateStartPlan"] == "2026-09-15" and body["dateEndPlan"] == "2026-09-30"


def test_invoice_body_roles_single_contract_and_price():
    body = invoice_body({
        "external_id": "inv-1", "contract_external_id": "ct-1", "number": "А-7",
        "invoice_date": "2026-10-20", "period_start": "2026-09-15", "period_end": "2026-10-15",
        "amount": "1234.5", "is_vat": True, "client_role": "agency", "contractor_role": "ors",
    })
    assert body["externalInvoiceId"] == "inv-1" and body["externalContractId"] == "ct-1"
    assert body["invoiceNumber"] == "А-7" and body["invoiceDate"] == "2026-10-20"
    assert body["startDate"] == "2026-09-15" and body["endDate"] == "2026-10-15"
    assert body["price"] == "1234.50" and body["withNds"] is True
    assert body["clientRole"] == "ORGANISATION_ROLE_RA" and body["contractorRole"] == "ORGANISATION_ROLE_ORS"
    assert body["contracts"] == [{"externalContractId": "ct-1", "price": "1234.50", "withNds": True}]


def test_invoice_link_and_unlink_bodies():
    data = {"external_invoice_id": "inv-1", "contract_external_id": "ct-1", "external_creative_ids": ["a:v1", "b:v2"]}
    assert invoice_link_body(data) == {"items": [
        {"externalInvoiceId": "inv-1", "externalContractId": "ct-1", "externalCreativeIds": ["a:v1", "b:v2"]}
    ]}
    assert invoice_unlink_all_body(data) == {"items": [
        {"externalInvoiceId": "inv-1", "externalContractId": "ct-1", "deleteAllCreatives": True}
    ]}


def test_month_slices_split_invoice_period_by_calendar_month():
    from datetime import date

    assert month_slices(date(2026, 9, 15), date(2026, 10, 15)) == [
        ("2026-09", date(2026, 9, 15), date(2026, 9, 30)),
        ("2026-10", date(2026, 10, 1), date(2026, 10, 15)),
    ]
    assert month_slices(date(2026, 2, 1), date(2026, 2, 28)) == [("2026-02", date(2026, 2, 1), date(2026, 2, 28))]
    assert [p for p, _, _ in month_slices(date(2026, 11, 30), date(2027, 1, 2))] == ["2026-11", "2026-12", "2027-01"]


def test_unit_cost_cpm_and_cpc():
    assert str(unit_cost("cpm", 2000, 5, "30")) == "15.0000"
    assert str(unit_cost("cpc", 2000, 5, "30")) == "6.0000"
    assert str(unit_cost("cpm", 0, 0, "30")) == "0.0000"
    assert str(unit_cost("cpc", 100, 0, "30")) == "0.0000"


def test_ozon_invoice_flow_uses_v2_invoice_and_v3_link_paths():
    calls: list[tuple[str, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.url.path, json.loads(req.content)))
        if req.url.path.endswith("/creative/unlink"):
            return httpx.Response(400, json={"error": "no links"})  # у нового акта связей нет
        return httpx.Response(200, json={"invoice": {"externalInvoiceId": "inv-1"}})

    c = _client_with(handler)
    out = asyncio.run(c.register_invoice({
        "external_id": "inv-1", "contract_external_id": "ct-1", "number": "1", "invoice_date": "2026-10-01",
        "period_start": "2026-09-01", "period_end": "2026-09-30", "amount": 10, "is_vat": True,
    }))
    assert out == {"external_id": "inv-1"}
    asyncio.run(c.link_invoice_creatives(
        {"external_invoice_id": "inv-1", "contract_external_id": "ct-1", "external_creative_ids": ["a:v1"]}
    ))
    paths = [p for p, _ in calls]
    assert paths == ["/api/external/v2/invoice", "/api/external/v3/invoice/creative/unlink",
                     "/api/external/v3/invoice/creative/link"]
    assert calls[2][1]["items"][0]["externalCreativeIds"] == ["a:v1"]


def test_ozon_invoice_4xx_is_data_error_5xx_retried_then_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "invoiceNumber required"})

    c = _client_with(handler)
    with pytest.raises(OrdDataError):
        asyncio.run(c.register_invoice({
            "external_id": "inv-1", "contract_external_id": "ct-1", "number": "", "invoice_date": "2026-10-01",
            "period_start": "2026-09-01", "period_end": "2026-09-30", "amount": 10,
        }))
