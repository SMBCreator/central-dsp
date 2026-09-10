"""Маппинг наших сущностей в модели API ОРД Ozon (api-ord.ozon.ru).

Источник: спецификация Ozon ОРД (/api/external/*), сверена с открытым
клиентом frollow/ozon-ord (2024) — публичная документация доступна только из
личного кабинета. Пункты, которые надо перепроверить с реальным ключом,
помечены "[V]" в docs/audit/ord_spec.md.

Все функции чистые: dict -> dict, без сети и БД — покрыты unit-тестами.
"""
from __future__ import annotations

import hashlib
from typing import Any

# --- контрагент -------------------------------------------------------------
LEGAL_TYPE = {
    "ul": "LEGAL_TYPE_LEGAL",
    "ip": "LEGAL_TYPE_ENTREPRENEUR",
    "fl": "LEGAL_TYPE_INDIVIDUAL",
    "foreign": "LEGAL_TYPE_FOREIGN_LEGAL",
}

# Роль контрагента в цепочке («проводка»): РД — рекламодатель, РА — агентство,
# РР — рекламораспространитель, ОРС — оператор рекламной системы (мы, DSP).
ORGANISATION_ROLE = {
    "advertiser": "ORGANISATION_ROLE_RD",
    "agency": "ORGANISATION_ROLE_RA",
    "publisher": "ORGANISATION_ROLE_RR",
    "ors": "ORGANISATION_ROLE_ORS",
}

# --- договор ----------------------------------------------------------------
CONTRACT_TYPE = {
    "service": "CONTRACT_TYPE_SERVICE",
    "mediation": "CONTRACT_TYPE_INTERMEDIARY",
    "additional": "CONTRACT_TYPE_ADDITIONAL_AGREEMENT",
}
SUBJECT_TYPE = {
    "distribution": "SUBJECT_TYPE_DISTRIBUTION",
    "org_distribution": "SUBJECT_TYPE_ORGANISATION",
    "mediation": "SUBJECT_TYPE_INTERMEDIARY",
    "representation": "SUBJECT_TYPE_REPRESENTATION",
    "other": "SUBJECT_TYPE_OTHER",
}

# --- креатив ----------------------------------------------------------------
ADV_OBJECT_TYPE = {
    "banner": "ADV_OBJECT_TYPE_BANNER",
    "video": "ADV_OBJECT_TYPE_VIDEO",
    "native": "ADV_OBJECT_TYPE_TEXT_GRAPHIC_BLOCK",
    "push": "ADV_OBJECT_TYPE_TEXT_GRAPHIC_BLOCK",
    "html5": "ADV_OBJECT_TYPE_BANNER",
}
PAYMENT_TYPE = {"cpm": "PAYMENT_TYPE_CPM", "cpc": "PAYMENT_TYPE_CPC", "cpa": "PAYMENT_TYPE_CPA"}


def _address(data: dict[str, Any]) -> dict[str, str]:
    return {
        "address": data.get("address") or "",
        "locality": data.get("locality") or "",
        "postcode": data.get("postcode") or "",
    }


def organisation_body(data: dict[str, Any]) -> dict[str, Any]:
    """counterparties -> POST /api/external/organisation."""
    body: dict[str, Any] = {
        "externalOrganisationId": data["external_id"],
        "inn": data["inn"],
        "fullOpf": data["name"],
        "legalType": LEGAL_TYPE.get(data.get("legal_type") or "ul", "LEGAL_TYPE_LEGAL"),
        "isOpc": False,
        "isPp": False,
        "legalAddress": _address(data),
        "postAddress": _address(data),
    }
    if data.get("ogrn"):
        key = "ogrnip" if data.get("legal_type") == "ip" else "ogrn"
        body[key] = data["ogrn"]
    if data.get("legal_type") == "fl":
        body["fio"] = data["name"]
    return body


def contract_body(data: dict[str, Any]) -> dict[str, Any]:
    """ord_contracts -> POST /api/external/contract.

    Заказчик/исполнитель передаются внешними ID их организаций — без них ОРД не
    свяжет договор с контрагентами (audit L-03). externalParentId — родительский
    договор цепочки (агентский -> изначальный).
    """
    body: dict[str, Any] = {
        "externalContractId": data["external_id"],
        "externalOrganisationCustomerId": data["client_external_id"],
        "externalOrganisationPerformerId": data["contractor_external_id"],
        "contractType": CONTRACT_TYPE.get(data.get("contract_type") or "service", "CONTRACT_TYPE_SERVICE"),
        "subjectType": SUBJECT_TYPE.get(data.get("subject_type") or "distribution", "SUBJECT_TYPE_DISTRIBUTION"),
        "actionType": "ACTION_TYPE_DISTRIBUTION",
        "contractNumber": data.get("number") or "",
        "contractDate": str(data["signed_at"]),
        "withNds": bool(data.get("is_vat", True)),
        "isCreativeReporter": True,
    }
    if data.get("amount") is not None:
        body["price"] = str(data["amount"])
    if data.get("parent_external_id"):
        body["externalParentId"] = data["parent_external_id"]
    return body


def _file_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:32]


def creative_body(data: dict[str, Any]) -> dict[str, Any]:
    """Провайдеро-нейтральный payload (ord/service.py::_payload) -> POST /api/external/creative.

    externalCreativeId = '<creative_id>:v<version>' — каждая версия содержимого
    получает собственный erid, старый остаётся в истории (ord_creatives).
    """
    media: list[dict[str, Any]] = []
    for url in data.get("media_urls") or []:
        media.append({"description": data["name"], "file": {"id": _file_id(url), "originalUrl": url, "url": url}})
    for text in data.get("texts") or []:
        media.append({"text": text})
    if data.get("html_snippet"):
        media.append({"description": "html5", "text": data["html_snippet"]})
    if data.get("vast_xml"):
        media.append({"description": "vast", "text": data["vast_xml"]})
    if not media:
        media.append({"text": data.get("description") or data["name"]})
    body: dict[str, Any] = {
        "externalCreativeId": data["external_creative_id"],
        "externalContractIds": [data["contract_external_id"]],
        "title": data["name"],
        "description": data.get("description") or data["name"],
        "advObjectType": ADV_OBJECT_TYPE.get(data.get("type") or "banner", "ADV_OBJECT_TYPE_BANNER"),
        "paymentType": PAYMENT_TYPE.get(data.get("payment_type") or "cpm", "PAYMENT_TYPE_CPM"),
        "okvedCodes": [data["kktu"]] if data.get("kktu") else [],
        "isSocialAdv": False,
        "isNative": (data.get("type") in ("native", "push")),
        "mediaData": media,
    }
    if data.get("click_url"):
        body["targetLink"] = data["click_url"]
        body["urlList"] = [{"url": data["click_url"]}]
    return body


def creative_erid(response: dict[str, Any]) -> str | None:
    """erid из ответа /api/external/creative: поле creative.marker (в некоторых
    версиях — erid на верхнем уровне)."""
    cr = response.get("creative") or {}
    return cr.get("marker") or cr.get("erid") or response.get("marker") or response.get("erid")


def statistic_body(period_start: str, period_end: str, row: dict[str, Any], platform_id: str) -> dict[str, Any]:
    """Строка месячной статистики -> элемент statistics[] для POST /api/external/v2/statistic.

    externalStatisticId = '<creative_id>:v<version>:<YYYY-MM>' — повторная
    отправка за тот же период = upsert (коррекция), а не дубль.
    unitCost — цена за единицу (CPM: за 1000 показов; CPC: за клик).
    """
    imps = int(row.get("impressions") or 0)
    spend = row.get("spend") or row.get("amount") or 0
    unit_cost = (float(spend) / imps * 1000) if imps else 0.0
    return {
        "externalStatisticId": row["external_statistic_id"],
        "externalCreativeId": row["external_creative_id"],
        "externalPlatformId": platform_id,
        "dateStartPlan": period_start,
        "dateEndPlan": period_end,
        "dateStartFact": period_start,
        "dateEndFact": period_end,
        "viewsCountByFact": str(imps),
        "viewsCountByInvoice": str(imps),
        "moneySpent": f"{float(spend):.2f}",
        "unitCost": f"{unit_cost:.4f}",
        "withNds": bool(row.get("with_vat", True)),
    }


def period_bounds(period: str) -> tuple[str, str]:
    """'YYYY-MM' -> ('YYYY-MM-01', 'YYYY-MM-<last>')."""
    import calendar

    year, month = int(period[:4]), int(period[5:7])
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"
