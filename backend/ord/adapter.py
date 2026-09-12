"""Интерфейс ОРД-адаптера + фабрика.

Контракт одинаковый для Ozon и mock:
  register_counterparty(data) -> {"external_id": ...}
  register_contract(data)     -> {"external_id": ...}
  register_creative(data)     -> {"external_id": ..., "erid": ...}
  register_platform(data)     -> {"external_id": ...}
  submit_stats(period, rows)  -> {"accepted": [ext_stat_id...], "rejected": [{"id", "error"}...]}
                                 (B13: построчный результат; 5xx/сеть — OrdError на всю выгрузку)
  register_invoice(data)      -> {"external_id": ...}            (B13b: акт, upsert)
  link_invoice_creatives(data)-> None   (пересобирает детализацию акта: unlink all + link)
  list_error_notifications(gt_id, page_size) -> [{"id", "object_id", "external_id",
                                 "object_type", "text", "date", "updated_at", "url"}]
                                 (B13g: ошибки ЕРИР после регистрации; id монотонный — курсор)

Все методы идемпотентны на нашей стороне: перед вызовом проверяется
ord_status в БД, повторная регистрация не выполняется.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Protocol


class OrdError(Exception):
    """Ошибка ОРД: сохраняется в ord_error/error, статус -> failed."""


class OrdDataError(OrdError):
    """4xx от ОРД: ошибка данных конкретного запроса, ретраить бессмысленно.
    Экспорт статистики по ней отклоняет строку, а не всю выгрузку."""


class OrdNotConfigured(OrdError):
    """Production без ключа ОРД (audit L-01).

    Mock-адаптер выдаёт фиктивные erid; на боевом трафике это нарушение 38-ФЗ
    с реальным штрафом. Вместо тихого fallback — явная ошибка: регистрация
    креатива падает в статус failed с понятной причиной, фид для РФ закрыт.
    """


MOCK_PROVIDER = "mock"


def is_production(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return env.get("APP_ENV", "").strip().lower() in ("production", "prod")


def mock_allowed(env: Mapping[str, str] | None = None) -> bool:
    """Mock разрешён только вне production. Никакого override-флага: если
    production-стенд должен работать без ОРД (нет РФ-трафика) — это
    ORD_REQUIRED=0 в startup_checks, а не фиктивные erid."""
    return not is_production(env)


def mock_erid_accepted(env: Mapping[str, str] | None = None) -> bool:
    """Пропускать ли креативы с mock-erid через erid-гейт фида/лаунча.
    Совпадает с mock_allowed: там, где mock не выдаётся, он и не принимается —
    старые mock-erid из dev-БД не должны попасть в production-торги."""
    return mock_allowed(env)


NOT_CONFIGURED_MESSAGE = (
    "ОРД не настроен: задайте API-ключ ОРД Озон в Настройках платформы "
    "(или ORD_OZON_API_KEY). В production фиктивные erid не выдаются."
)


class OrdAdapter(Protocol):
    provider: str

    async def register_counterparty(self, data: dict[str, Any]) -> dict[str, Any]: ...
    async def register_contract(self, data: dict[str, Any]) -> dict[str, Any]: ...
    async def register_creative(self, data: dict[str, Any]) -> dict[str, Any]: ...
    async def register_platform(self, data: dict[str, Any]) -> dict[str, Any]: ...
    async def submit_stats(self, period: str, rows: list[dict[str, Any]]) -> dict[str, Any]: ...
    async def register_invoice(self, data: dict[str, Any]) -> dict[str, Any]: ...
    async def link_invoice_creatives(self, data: dict[str, Any]) -> None: ...
    async def list_error_notifications(self, gt_id: int = 0, page_size: int = 100) -> list[dict[str, Any]]: ...


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
                    "platform_url": cfg.get("platform_url") or "",
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
    if not mock_allowed():
        raise OrdNotConfigured(NOT_CONFIGURED_MESSAGE)
    from .mock import MockOrdAdapter

    return MockOrdAdapter()
