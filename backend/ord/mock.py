"""Mock ОРД-адаптер: детерминированные erid без сети (dev/CI).

erid формата 'MOCK-<sha1[:12]>' — стабилен для одного и того же креатива,
что делает регистрацию идемпотентной даже без БД-состояния.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _stable_id(prefix: str, data: dict[str, Any]) -> str:
    digest = hashlib.sha1(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()[:12]
    return f"{prefix}-{digest}"


class MockOrdAdapter:
    provider = "mock"

    async def register_counterparty(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"external_id": data.get("external_id") or _stable_id("MOCKCP", data)}

    async def register_contract(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"external_id": data.get("external_id") or _stable_id("MOCKCT", data)}

    async def register_creative(self, data: dict[str, Any]) -> dict[str, Any]:
        ext = _stable_id("MOCKCR", data)
        return {"external_id": ext, "erid": f"MOCK-{ext[-12:]}"}

    async def register_platform(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"external_id": data["external_id"]}

    async def register_invoice(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"external_id": data["external_id"]}

    async def link_invoice_creatives(self, data: dict[str, Any]) -> None:
        return None

    async def submit_stats(self, period: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Принимает всё; строка с нулём показов (viewsCountByInvoice, а без
        акта — viewsCountByFact) отклоняется — как реальный ОРД, чтобы
        partial-путь экспорта и акта был проверяем без сети."""

        def _views(r: dict[str, Any]) -> int:
            inv = r.get("invoice_impressions")
            return int(r.get("impressions") or 0) if inv is None else int(inv)

        accepted = [r["external_statistic_id"] for r in rows if _views(r) > 0]
        rejected = [
            {"id": r["external_statistic_id"], "error": "viewsCount must be positive"}
            for r in rows if _views(r) <= 0
        ]
        return {"accepted": accepted, "rejected": rejected}
