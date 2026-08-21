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

from .adapter import OrdError

_RETRIES = 3
_TIMEOUT = 30.0


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
                    raise OrdError(f"ОРД Ozon {resp.status_code}: {resp.text[:500]}") from None
                return resp.json()
            except OrdError as e:
                if "5xx" not in str(e):
                    raise
                last_err = e
            except httpx.HTTPError as e:
                last_err = OrdError(f"ОРД Ozon network: {e}")
            await asyncio.sleep(2**attempt)
        raise last_err or OrdError("ОРД Ozon: неизвестная ошибка")

    # -- Пути ниже сверить со спецификацией при получении ключа --------------

    async def register_counterparty(self, data: dict[str, Any]) -> dict[str, Any]:
        out = await self._request("POST", "/api/external/v1/organisation", data)
        return {"external_id": out.get("id") or out.get("external_id")}

    async def register_contract(self, data: dict[str, Any]) -> dict[str, Any]:
        out = await self._request("POST", "/api/external/v1/contract", data)
        return {"external_id": out.get("id") or out.get("external_id")}

    async def register_creative(self, data: dict[str, Any]) -> dict[str, Any]:
        out = await self._request("POST", "/api/external/v1/creative", data)
        erid = out.get("erid") or out.get("marker") or (out.get("creative") or {}).get("erid")
        if not erid:
            raise OrdError(f"ОРД Ozon: ответ без erid: {str(out)[:300]}")
        return {"external_id": out.get("id") or out.get("external_id"), "erid": erid}

    async def submit_stats(self, period: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        out = await self._request(
            "POST", "/api/external/v1/statistics", {"period": period, "rows": rows}
        )
        return {"accepted": out.get("accepted", len(rows))}
