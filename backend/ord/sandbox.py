"""Сквозной прогон песочницы ОРД Ozon для администратора.

Проверяет адаптер на живом sandbox-API без участия БД кабинета:
синтетические организации/договор/креатив/статистика/акт создаются с
уникальными external ID прогона (`sbx-<run>-…`) и не пишутся в наши таблицы.
Тела собираются теми же функциями ord.ozon_mapping, что и в production, —
успешный прогон подтверждает именно боевой маппинг.

Безопасность:
* хост принудительно SANDBOX_BASE_URL — прод-ОРД этим кодом не вызвать;
* ключ живёт только в памяти запроса и вырезается из ответов;
* упавший шаг помечает зависимые как skipped, независимые выполняются.
"""
from __future__ import annotations

import random
import secrets
import time
import uuid
from datetime import date, timedelta
from typing import Any, Callable

import httpx

from .ozon_mapping import (
    contract_body,
    creative_body,
    creative_erid,
    invoice_body,
    invoice_link_body,
    organisation_body,
    period_bounds,
    platform_body,
    statistic_body,
)

SANDBOX_BASE_URL = "https://api-ord-sandbox.ozon.ru"
_TIMEOUT = 30.0
_BODY_LIMIT = 4000

STEPS: list[dict[str, Any]] = [
    {"key": "ping", "title": "Проверка ключа (список организаций)", "depends": []},
    {"key": "kktu", "title": "Справочник ККТУ (dict/kktu)", "depends": ["ping"]},
    {"key": "org_advertiser", "title": "Организация рекламодателя (РД)", "depends": ["ping"]},
    {"key": "org_self", "title": "Организация ОРС (мы)", "depends": ["ping"]},
    # C30 [V]: принимает ли ОРД иностранное ЮЛ с regionNumber/taxId/registrationNumber.
    # Вне цепочки договора — её ошибка не блокирует остальные шаги.
    {"key": "org_foreign", "title": "Иностранная организация (C30)", "depends": ["ping"]},
    {"key": "platform", "title": "Площадка ОРС", "depends": ["org_self"]},
    {"key": "contract", "title": "Договор РД → ОРС", "depends": ["org_advertiser", "org_self"]},
    {"key": "creative", "title": "Креатив и получение erid", "depends": ["contract"]},
    {"key": "statistic", "title": "Статистика показов за прошлый месяц", "depends": ["creative", "platform"]},
    {"key": "invoice", "title": "Акт по договору", "depends": ["contract"]},
    {"key": "invoice_link", "title": "Привязка креатива к акту", "depends": ["invoice", "creative"]},
    {"key": "notifications", "title": "Ошибки ЕРИР (error_list)", "depends": ["ping"]},
]
STEP_KEYS = [s["key"] for s in STEPS]

_INN10_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8)


def inn10_checksum_ok(inn: str) -> bool:
    if len(inn) != 10 or not inn.isdigit():
        return False
    d = [int(c) for c in inn]
    return sum(w * x for w, x in zip(_INN10_WEIGHTS, d)) % 11 % 10 == d[9]


def random_inn10(rng: random.Random | None = None) -> str:
    """Синтетический ИНН ЮЛ с корректной контрольной цифрой (регион 77)."""
    rng = rng or random.SystemRandom()
    digits = [7, 7] + [rng.randint(0, 9) for _ in range(7)]
    check = sum(w * x for w, x in zip(_INN10_WEIGHTS, digits)) % 11 % 10
    return "".join(map(str, digits + [check]))


def _prev_month(today: date) -> str:
    last = today.replace(day=1) - timedelta(days=1)
    return f"{last.year:04d}-{last.month:02d}"


def scrub(text: str, secret: str) -> str:
    return text.replace(secret, "•••") if secret else text


def build_context(params: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Синтетические данные прогона; все external ID уникальны для run_id."""
    today = today or date.today()
    run = params.get("run_id") or uuid.uuid4().hex[:8]
    pfx = f"sbx-{run}"
    period = _prev_month(today)
    p_start, p_end = period_bounds(period)
    url = params.get("platform_url") or "https://central-dsp.example"
    platform = {"external_id": f"{pfx}-platform", "name": "Central DSP sandbox",
                "platform_type": "system", "url": url}
    creative_id = f"{pfx}-creative:v1"
    foreign_tax_id = "".join(str(secrets.randbelow(10)) for _ in range(12))  # формат БИН Казахстана
    return {
        "run_id": run,
        "period": period,
        "period_start": p_start,
        "period_end": p_end,
        "advertiser": {
            "external_id": f"{pfx}-org-rd",
            "inn": params.get("advertiser_inn") or random_inn10(),
            "name": params.get("advertiser_name") or f"ООО «Тест РД {run}»",
            "legal_type": "ul",
            "role": "advertiser",
        },
        "self": {
            "external_id": f"{pfx}-org-ors",
            "inn": params.get("self_inn") or random_inn10(),
            "name": params.get("self_name") or f"ООО «Тест ОРС {run}»",
            "legal_type": "ul",
            "is_self": True,
            "rs_url": url,
            "platforms": [{**platform, "is_owner": True}],
        },
        "foreign": {
            "external_id": f"{pfx}-org-foreign",
            "inn": foreign_tax_id,
            "name": f"Test Foreign LLP {run}",
            "legal_type": "foreign",
            "role": "advertiser",
            "region_number": "398",
            "tax_id": foreign_tax_id,
            "registration_number": foreign_tax_id,
        },
        "platform": platform,
        "contract": {
            "external_id": f"{pfx}-contract",
            "client_external_id": f"{pfx}-org-rd",
            "contractor_external_id": f"{pfx}-org-ors",
            "contract_type": "service",
            "subject_type": "distribution",
            "number": f"SBX-{run}",
            "signed_at": p_start,
            "amount": "10000.00",
        },
        "creative": {
            "external_creative_id": creative_id,
            "contract_external_id": f"{pfx}-contract",
            "name": f"Тестовый креатив {run}",
            "description": "Проверка интеграции DSP с ОРД (песочница)",
            "type": "banner",
            "kktu": params.get("kktu") or "30.15.1",
            "click_url": url,
            "texts": ["Тестовый баннер"],
        },
        "statistic": {
            "external_statistic_id": f"{creative_id}:{period}",
            "external_creative_id": creative_id,
            "impressions": 1000,
            "clicks": 10,
            "spend": "1220.00",
            "payment_type": "cpm",
            "with_vat": True,
        },
        "invoice": {
            "external_id": f"{pfx}-invoice",
            "contract_external_id": f"{pfx}-contract",
            "number": f"SBX-{run}-1",
            "invoice_date": p_end,
            "period_start": p_start,
            "period_end": p_end,
            "amount_gross": "1220.00",
            "is_vat": True,
            "vat_rate": 22,
            "client_role": "advertiser",
            "contractor_role": "ors",
        },
    }


def _http_hint(code: int) -> str:
    if code in (401, 403):
        return f"HTTP {code}: ключ отклонён — нужен ключ именно песочницы"
    if code == 404:
        return "HTTP 404: метод не найден — сверить путь со swagger"
    if 400 <= code < 500:
        return f"HTTP {code}: ОРД отклонил данные — см. ответ"
    return f"HTTP {code}: ошибка на стороне ОРД"


def _kktu_items(data: Any) -> list[dict[str, Any]] | None:
    items = data if isinstance(data, list) else (data.get("items") if isinstance(data, dict) else None)
    if not isinstance(items, list):
        return None
    return [
        {"code": str(i["code"]), "name": i.get("name") or "", "level": i.get("level"),
         "parent_code": i.get("parentCode"), "usable": bool(i.get("isUsableCategory", True)),
         "deleted": bool(i.get("deletedAt"))}
        for i in items if isinstance(i, dict) and i.get("code")
    ]


class SandboxRunner:
    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._key = api_key
        self._transport = transport
        self._clock = clock

    async def _call(self, method: str, path: str, body: Any = None, params: dict | None = None,
                    accept: str | None = None) -> tuple[int, Any, str]:
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        if accept:
            headers["accept"] = accept
        async with httpx.AsyncClient(base_url=SANDBOX_BASE_URL, timeout=_TIMEOUT,
                                     transport=self._transport) as client:
            resp = await client.request(method, path, json=body, params=params, headers=headers)
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = None
        return resp.status_code, data, scrub(resp.text, self._key)

    def _requests(self, ctx: dict[str, Any]) -> dict[str, tuple[str, str, Any, dict | None]]:
        """key -> (method, path, json body, query params)."""
        return {
            "ping": ("POST", "/api/external/v6/organisation/list", {"pageSize": 1}, None),
            "kktu": ("POST", "/api/external/dict/kktu", {}, None),
            "org_advertiser": ("POST", "/api/external/v6/organisation", organisation_body(ctx["advertiser"]), None),
            "org_self": ("POST", "/api/external/v6/organisation", organisation_body(ctx["self"]), None),
            "org_foreign": ("POST", "/api/external/v6/organisation", organisation_body(ctx["foreign"]), None),
            "platform": ("POST", "/api/external/v6/platform", platform_body(ctx["platform"]), None),
            "contract": ("POST", "/api/external/v6/contract", contract_body(ctx["contract"]), None),
            "creative": ("POST", "/api/external/v4/creative", creative_body(ctx["creative"]), None),
            "statistic": ("POST", "/api/external/v6/statistic", {"statistics": [statistic_body(
                ctx["period_start"], ctx["period_end"], ctx["statistic"], ctx["platform"]["external_id"])]}, None),
            "invoice": ("POST", "/api/external/v7/invoice", invoice_body(ctx["invoice"]), None),
            "invoice_link": ("POST", "/api/external/v3/invoice/creative/link", invoice_link_body({
                "external_invoice_id": ctx["invoice"]["external_id"],
                "contract_external_id": ctx["contract"]["external_id"],
                "external_creative_ids": [ctx["creative"]["external_creative_id"]],
            }), None),
            "notifications": ("GET", "/api/external/notification/error_list", None,
                              {"pageSize": 20, "gtNotificationId": "0"}),
        }

    async def run(self, ctx: dict[str, Any], only: list[str] | None = None) -> list[dict[str, Any]]:
        selected = [k for k in STEP_KEYS if not only or k in only]
        requests = self._requests(ctx)
        results: dict[str, dict[str, Any]] = {}
        for spec in STEPS:
            key = spec["key"]
            if key not in selected:
                continue
            base = {"key": key, "title": spec["title"]}
            unmet = [d for d in spec["depends"] if results.get(d, {}).get("status") != "ok"]
            if unmet:
                results[key] = {**base, "status": "skipped",
                                "error": "Пропущен: нужен успешный шаг " + ", ".join(unmet)}
                continue
            method, path, body, params = requests[key]
            t0 = self._clock()
            try:
                code, data, text = await self._call(
                    method, path, body, params, accept="application/json" if key == "kktu" else None)
            except httpx.HTTPError as e:
                results[key] = {**base, "status": "error", "ms": int((self._clock() - t0) * 1000),
                                "request": {"method": method, "path": path, "body": body if body is not None else params},
                                "error": scrub(f"Сеть: {e}", self._key)}
                continue
            out: dict[str, Any] = {
                **base, "http_status": code, "ms": int((self._clock() - t0) * 1000),
                "request": {"method": method, "path": path, "body": body if body is not None else params},
                "response": text[:_BODY_LIMIT],
            }
            err = None if 200 <= code < 300 else _http_hint(code)
            if err is None and key == "creative":
                out["erid"] = creative_erid(data) if isinstance(data, dict) else None
                if not out["erid"]:
                    err = "Ответ без erid (creative.marker)"
            if err is None and key == "kktu":
                items = _kktu_items(data)
                if items is None:
                    err = "Ответ справочника не массив"
                else:
                    out["kktu"] = items
                    out["response"] = f"{len(items)} записей"
            out["status"] = "ok" if err is None else "error"
            if err:
                out["error"] = err
            results[key] = out
        return [results[k] for k in selected]
