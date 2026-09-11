"""Клиент ОРД Ozon API.

ВНИМАНИЕ: пути эндпоинтов заданы по публичной документации ОРД Ozon
(api-ord.ozon.ru) и помечены для финальной сверки, когда пользователь
передаст API-ключ и доступ к актуальной спецификации/песочнице.

Retry: 3 попытки с экспоненциальной задержкой на 5xx/сетевых ошибках;
4xx не ретраятся (ошибка данных) и поднимают OrdError сразу.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .adapter import OrdDataError, OrdError
from .ozon_mapping import (
    contract_body,
    creative_body,
    creative_erid,
    invoice_body,
    invoice_link_body,
    invoice_unlink_all_body,
    organisation_body,
    period_bounds,
    platform_body,
    statistic_body,
)
from .platform import platform_external_id

_RETRIES = 3
_TIMEOUT = 30.0
_STATS_BATCH = 100


class OzonOrdClient:
    provider = "ozon"

    def __init__(self, api_key: str, base_url: str = "https://api-ord.ozon.ru") -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_err: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.request(
                        method, f"{self._base}{path}", json=payload, headers=self._headers
                    )
                if resp.status_code >= 500:
                    raise OrdError(f"ОРД Ozon 5xx: {resp.status_code} {resp.text[:300]}")
                if resp.status_code >= 400:
                    # ошибка данных — не ретраим
                    raise OrdDataError(f"ОРД Ozon {resp.status_code}: {resp.text[:500]}") from None
                return resp.json()
            except OrdDataError:
                raise
            except OrdError as e:
                last_err = e
            except httpx.HTTPError as e:
                last_err = OrdError(f"ОРД Ozon network: {e}")
            await asyncio.sleep(2**attempt)
        raise last_err or OrdError("ОРД Ozon: неизвестная ошибка")


    async def ping(self) -> dict:
        """Проверка ключа: лёгкий GET списка организаций. 401/403 => ключ неверный."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._base}/api/external/organisation/list",
                    json={"pageSize": 1},
                    headers=self._headers,
                )
            if resp.status_code in (401, 403):
                raise OrdError(f"Ключ отклонён ({resp.status_code}): проверьте API-ключ и окружение")
            if resp.status_code >= 400:
                raise OrdError(f"ОРД Ozon {resp.status_code}: {resp.text[:300]}")
            return {"ok": True}
        except httpx.HTTPError as e:
            raise OrdError(f"ОРД Ozon недоступен: {e}")

    # -- Реальные пути API Ozon ОРД (см. ord/ozon_mapping.py, ord_spec.md [V]) --

    async def register_counterparty(self, data: dict[str, Any]) -> dict[str, Any]:
        await self._request("POST", "/api/external/organisation", organisation_body(data))
        return {"external_id": data["external_id"]}

    async def register_contract(self, data: dict[str, Any]) -> dict[str, Any]:
        await self._request("POST", "/api/external/contract", contract_body(data))
        return {"external_id": data["external_id"]}

    async def register_creative(self, data: dict[str, Any]) -> dict[str, Any]:
        out = await self._request("POST", "/api/external/creative", creative_body(data))
        erid = creative_erid(out)
        if not erid:
            raise OrdError(f"ОРД Ozon: ответ без erid: {str(out)[:300]}")
        return {"external_id": data["external_creative_id"], "erid": erid}

    async def register_platform(self, data: dict[str, Any]) -> dict[str, Any]:
        await self._request("POST", "/api/external/platform", platform_body(data))
        return {"external_id": data["external_id"]}

    async def register_invoice(self, data: dict[str, Any]) -> dict[str, Any]:
        """Акт: POST /api/external/v2/invoice — upsert по externalInvoiceId,
        повторная отправка = редактирование акта."""
        await self._request("POST", "/api/external/v2/invoice", invoice_body(data))
        return {"external_id": data["external_id"]}

    async def link_invoice_creatives(self, data: dict[str, Any]) -> None:
        """Детализация акта пересобирается целиком: unlink(deleteAllCreatives) →
        link актуального набора. 4xx на unlink у нового акта (связей ещё нет)
        не считается ошибкой."""
        try:
            await self._request("POST", "/api/external/v3/invoice/creative/unlink", invoice_unlink_all_body(data))
        except OrdDataError:
            pass
        if data["external_creative_ids"]:
            await self._request("POST", "/api/external/v3/invoice/creative/link", invoice_link_body(data))

    async def submit_stats(self, period: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Статистика за месяц: батчи по _STATS_BATCH; батч, отклонённый 4xx,
        досылается построчно, чтобы отделить принятые строки от отклонённых.
        Возвращает {"accepted": [ext_id...], "rejected": [{"id", "error"}...]}."""
        start, end = period_bounds(period)
        platform_id = platform_external_id()
        accepted: list[str] = []
        rejected: list[dict[str, str]] = []

        async def _send(batch: list[dict[str, Any]]) -> None:
            body = {"statistics": [statistic_body(start, end, r, platform_id) for r in batch]}
            await self._request("POST", "/api/external/v2/statistic", body)

        for i in range(0, len(rows), _STATS_BATCH):
            batch = rows[i:i + _STATS_BATCH]
            try:
                await _send(batch)
                accepted.extend(r["external_statistic_id"] for r in batch)
                continue
            except OrdDataError as e:
                if len(batch) == 1:
                    rejected.append({"id": batch[0]["external_statistic_id"], "error": str(e)})
                    continue
            # 4xx на батче из нескольких строк: ищем виновника построчно
            for r in batch:
                try:
                    await _send([r])
                    accepted.append(r["external_statistic_id"])
                except OrdDataError as e:
                    rejected.append({"id": r["external_statistic_id"], "error": str(e)})
        return {"accepted": accepted, "rejected": rejected}
