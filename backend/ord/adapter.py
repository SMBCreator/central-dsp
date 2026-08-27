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


async def get_ord_settings() -> dict:
    """Настройки ОРД: сперва БД (platform_settings.ord_ozon), затем env."""
    try:
        from db import get_pool

        pool = await get_pool()
        raw = await pool.fetchval("SELECT value FROM platform_settings WHERE key = 'ord_ozon'")
        if raw:
            import json as _json

            cfg = raw if isinstance(raw, dict) else _json.loads(raw)
            if cfg.get("api_key"):
                return {
                    "api_key": cfg["api_key"],
                    "base_url": cfg.get("base_url") or "https://api-ord.ozon.ru",
                    "source": "db",
                }
    except Exception:
        pass  # таблицы может не быть в старых тестовых БД — падать нельзя
    if os.getenv("ORD_OZON_API_KEY"):
        return {
            "api_key": os.environ["ORD_OZON_API_KEY"],
            "base_url": os.getenv("ORD_OZON_BASE_URL", "https://api-ord.ozon.ru"),
            "source": "env",
        }
    return {}


async def get_ord_adapter() -> OrdAdapter:
    cfg = await get_ord_settings()
    if cfg.get("api_key"):
        from .ozon import OzonOrdClient

        return OzonOrdClient(api_key=cfg["api_key"], base_url=cfg["base_url"])
    from .mock import MockOrdAdapter

    return MockOrdAdapter()
