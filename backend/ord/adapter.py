"""Интерфейс ОРД-адаптера + фабрика.

Контракт одинаковый для Ozon и mock:
  register_counterparty(data) -> {"external_id": ...}
  register_contract(data)     -> {"external_id": ...}
  register_creative(data)     -> {"external_id": ..., "erid": ...}
  submit_stats(period, rows)  -> {"accepted": N}

Все методы идемпотентны на нашей стороне: перед вызовом проверяется
ord_status в БД, повторная регистрация не выполняется.
"""
from __future__ import annotations

import os
from typing import Any, Protocol


class OrdError(Exception):
    """Ошибка ОРД: сохраняется в ord_error/error, статус -> failed."""


class OrdAdapter(Protocol):
    provider: str

    async def register_counterparty(self, data: dict[str, Any]) -> dict[str, Any]: ...
    async def register_contract(self, data: dict[str, Any]) -> dict[str, Any]: ...
    async def register_creative(self, data: dict[str, Any]) -> dict[str, Any]: ...
    async def submit_stats(self, period: str, rows: list[dict[str, Any]]) -> dict[str, Any]: ...


def get_ord_adapter() -> OrdAdapter:
    if os.getenv("ORD_OZON_API_KEY"):
        from .ozon import OzonOrdClient

        return OzonOrdClient(
            api_key=os.environ["ORD_OZON_API_KEY"],
            base_url=os.getenv("ORD_OZON_BASE_URL", "https://api-ord.ozon.ru"),
        )
    from .mock import MockOrdAdapter

    return MockOrdAdapter()
