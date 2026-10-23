"""Раздел C: тела запросов адаптера Ozon ОРД соответствуют docs/ord/swagger_ord_ozon.json.

Валидатор проверяет то, на чём ломается grpc-gateway: путь+метод существуют,
нет неизвестных полей, все required на месте, enum-значения допустимы,
типы (string/boolean/integer/array/object) совпадают. Без сети.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from ord import ozon as ozon_mod
from ord.ozon import OzonOrdClient
from ord.ozon_mapping import (
    contract_body,
    creative_body,
    invoice_body,
    invoice_link_body,
    invoice_unlink_all_body,
    organisation_body,
    platform_body,
    statistic_body,
)

SPEC = json.loads((Path(__file__).resolve().parents[2] / "docs/ord/swagger_ord_ozon.json").read_text())
SCHEMAS = SPEC["components"]["schemas"]
PY_TYPES = {"string": str, "boolean": bool, "integer": int, "array": list, "object": dict}


def _deref(s: dict) -> dict:
    while "$ref" in s or ("allOf" in s and len(s["allOf"]) == 1):
        s = SCHEMAS[s["$ref"].split("/")[-1]] if "$ref" in s else s["allOf"][0]
    return s


def validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    s = _deref(schema)
    errs: list[str] = []
    if "enum" in s:
        allowed = [str(e).rstrip(",") for e in s["enum"]]
        if str(value) not in allowed:
            errs.append(f"{path}: {value!r} not in enum")
        return errs
    t = s.get("type") or ("object" if "properties" in s else None)
    if t and not isinstance(value, PY_TYPES[t]) or (t == "integer" and isinstance(value, bool)):
        return [f"{path}: expected {t}, got {type(value).__name__}"]
    if t == "array":
        for i, v in enumerate(value):
            errs += validate(v, s.get("items", {}), f"{path}[{i}]")
    if t == "object" or "properties" in s:
        props = s.get("properties") or {}
        for k, v in value.items():
            if k not in props:
                errs.append(f"{path}.{k}: unknown field")
            else:
                errs += validate(v, props[k], f"{path}.{k}")
        for k in s.get("required") or []:
            if k not in value:
                errs.append(f"{path}.{k}: required")
    return errs


def request_schema(method: str, path: str) -> dict:
    op = SPEC["paths"][path][method.lower()]
    return op["requestBody"]["content"]["application/json"]["schema"]


def assert_valid(method: str, path: str, body: dict) -> None:
    errs = validate(body, request_schema(method, path))
    assert not errs, "\n".join(errs)


ORG = {"external_id": "org-1", "inn": "7700000000", "name": "ООО Ромашка", "legal_type": "ul",
       "ogrn": "1027700000001", "role": "advertiser", "address": "Москва, ул. Пушкина, 1"}
SELF_ORG = {**ORG, "external_id": "self", "is_self": True, "rs_url": "https://dsp.example.ru",
            "platforms": [{"external_id": "pl-1", "name": "DSP", "url": "https://dsp.example.ru"}]}
CONTRACT = {"external_id": "ct-1", "client_external_id": "org-1", "contractor_external_id": "self",
            "contract_type": "service", "number": "1", "signed_at": "2026-01-15", "amount": "1000",
            "parent_external_id": "ct-0"}
CREATIVE = {"external_creative_id": "cr-1:v1", "contract_external_id": "ct-1", "name": "Баннер",
            "type": "banner", "kktu": "30.10.1", "media_urls": ["https://cdn.example.ru/a.png"],
            "texts": ["Купите"], "click_url": "https://shop.example.ru"}
STAT = {"external_statistic_id": "cr-1:v1:2026-07", "external_creative_id": "cr-1:v1",
        "impressions": 2000, "clicks": 4, "spend": "30.00"}
INVOICE = {"external_id": "inv-1", "contract_external_id": "ct-exec", "number": "A-1",
           "invoice_date": "2026-07-31", "period_start": "2026-07-01", "period_end": "2026-07-31",
           "amount_gross": "1220.00", "amount_net": "1000.00", "vat_rate": 22, "is_vat": True,
           "contracts": [{"contract_external_id": "ct-1", "amount_gross": "1220.00",
                          "amount_net": "1000.00", "vat_rate": 22}]}
LINK = {"external_invoice_id": "inv-1", "contract_external_id": "ct-exec",
        "groups": [{"contract_external_id": "ct-1", "external_creative_ids": ["cr-1:v1"]}]}


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/external/v6/organisation", organisation_body(ORG)),
    ("POST", "/api/external/v6/organisation", organisation_body(SELF_ORG)),
    ("POST", "/api/external/v6/organisation", organisation_body({**ORG, "legal_type": "ip"})),
    ("POST", "/api/external/v6/organisation", organisation_body({**ORG, "legal_type": "fl", "address": None})),
    ("POST", "/api/external/v6/platform", platform_body({"external_id": "pl-1", "name": "DSP", "url": "https://x"})),
    ("POST", "/api/external/v6/contract", contract_body(CONTRACT)),
    ("POST", "/api/external/v4/creative", creative_body(CREATIVE)),
    ("POST", "/api/external/v4/creative", creative_body({**CREATIVE, "type": "video", "vast_xml": "<VAST/>"})),
    ("POST", "/api/external/v4/creative", creative_body({**CREATIVE, "type": "html5", "html_snippet": "<div/>"})),
    ("POST", "/api/external/v6/statistic",
     {"statistics": [statistic_body("2026-07-01", "2026-07-31", STAT, "pl-1")]}),
    ("POST", "/api/external/v6/statistic",
     {"statistics": [statistic_body("2026-07-01", "2026-07-31", {**STAT, "payment_type": "cpc",
                                                                   "with_vat": False}, "pl-1")]}),
    ("POST", "/api/external/v7/invoice", invoice_body(INVOICE)),
    ("POST", "/api/external/v7/invoice", invoice_body({**INVOICE, "contracts": None, "is_vat": False})),
    ("POST", "/api/external/v3/invoice/creative/link", invoice_link_body(LINK)),
    ("POST", "/api/external/v3/invoice/creative/unlink", invoice_unlink_all_body(LINK)),
])
def test_body_matches_swagger(method, path, body):
    assert_valid(method, path, body)


def test_validator_catches_legacy_shapes():
    """Самопроверка валидатора на прежних (v2) формах тел."""
    legacy_org = {"externalOrganisationId": "o", "inn": "1", "fullOpf": "x", "isOpc": False, "isPp": False}
    errs = validate(legacy_org, request_schema("POST", "/api/external/v6/organisation"))
    assert any("fullOpf: unknown" in e for e in errs) and any(".name: required" in e for e in errs)
    legacy_stat = {"statistics": [{"externalStatisticId": "s", "moneySpent": "1.00", "withNds": True}]}
    errs = validate(legacy_stat, request_schema("POST", "/api/external/v6/statistic"))
    assert any("moneySpent: unknown" in e for e in errs) and any("paymentType: required" in e for e in errs)


def test_statistic_price_and_payment_type():
    row = statistic_body("2026-07-01", "2026-07-31", STAT, "pl-1")
    assert row["paymentType"] == "PAYMENT_TYPE_CPM"
    assert row["price"] == {"amount": "30.00", "excludingAmount": "24.59", "vatRate": "22"}
    assert row["unitCost"] == "15.0000"
    cpc = statistic_body("2026-07-01", "2026-07-31", {**STAT, "payment_type": "cpc"}, "pl-1")
    assert cpc["paymentType"] == "PAYMENT_TYPE_CPC" and cpc["unitCost"] == "7.5000"
    no_vat = statistic_body("2026-07-01", "2026-07-31", {**STAT, "with_vat": False}, "pl-1")
    assert no_vat["price"] == {"amount": "30.00", "excludingAmount": "30.00", "vatRate": "100"}


def test_invoice_v7_prices_use_stored_net():
    body = invoice_body(INVOICE)
    assert body["servicePrice"] == {"amount": "1220.00", "excludingAmount": "1000.00", "vatRate": "22"}
    assert body["contracts"][0]["price"] == {"amount": "1220.00", "excludingAmount": "1000.00", "vatRate": "22"}
    assert body["invoiceType"] == "INVOICE_TYPE_INVOICE"


def test_unsupported_vat_rate_is_data_error():
    from ord.adapter import OrdDataError

    with pytest.raises(OrdDataError):
        invoice_body({**INVOICE, "vat_rate": 18, "contracts": None, "amount_net": None})


def test_creative_v4_kktu_and_media():
    body = creative_body(CREATIVE)
    assert body["kktuCategory"] == ["30.10.1"]
    assert body["mediaData"][0] == {"file": {"originalUrl": "https://cdn.example.ru/a.png",
                                             "fileType": "MEDIA_FILE_TYPE_IMAGE"}}
    assert "okvedCodes" not in body and "paymentType" not in body


_real_sleep = asyncio.sleep


async def _no_sleep(*_: Any) -> None:
    await _real_sleep(0)


def _run_client(handler) -> tuple[OzonOrdClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda req: (seen.append(req), handler(req))[1])
    orig = httpx.AsyncClient

    class _C(orig):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    ozon_mod.httpx.AsyncClient = _C  # type: ignore[misc]
    return OzonOrdClient("k", base_url="https://api-ord-sandbox.ozon.ru"), seen


@pytest.fixture
def restore_httpx():
    orig = httpx.AsyncClient
    yield
    ozon_mod.httpx.AsyncClient = orig  # type: ignore[misc]


def test_every_client_call_hits_a_swagger_path(restore_httpx, monkeypatch):
    monkeypatch.setattr(ozon_mod.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(ozon_mod, "platform_external_id", lambda: "pl-1")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/creative"):
            return httpx.Response(200, json={"creative": {"marker": "2SDnjXXX"}})
        if req.url.path.endswith("/error_list"):
            return httpx.Response(200, json={"notification": [{"id": "7", "text": "x"}]})
        return httpx.Response(200, json={})

    client, seen = _run_client(handler)

    async def go():
        await client.ping()
        await client.register_counterparty(ORG)
        await client.register_platform({"external_id": "pl-1", "name": "DSP", "url": "https://x"})
        await client.register_contract(CONTRACT)
        await client.register_creative(CREATIVE)
        await client.submit_stats("2026-07", [STAT])
        await client.register_invoice(INVOICE)
        await client.link_invoice_creatives(LINK)
        return await client.list_error_notifications(gt_id=5, page_size=50)

    notes = asyncio.run(go())
    assert notes[0]["id"] == 7
    for req in seen:
        assert req.method.lower() in SPEC["paths"].get(req.url.path, {}), f"{req.method} {req.url.path} not in swagger"
        if req.content:
            assert_valid(req.method, req.url.path, json.loads(req.content))
    el = seen[-1]
    assert el.method == "GET" and el.url.params["gtNotificationId"] == "5" and el.url.params["pageSize"] == "50"
    assert all(r.headers["authorization"] == "Bearer k" for r in seen)


def test_429_is_retried_then_succeeds(restore_httpx, monkeypatch):
    monkeypatch.setattr(ozon_mod.asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429, json={"error": "rate"}) if calls["n"] == 1 else httpx.Response(200, json={})

    client, _ = _run_client(handler)
    asyncio.run(client.register_contract(CONTRACT))
    assert calls["n"] == 2
