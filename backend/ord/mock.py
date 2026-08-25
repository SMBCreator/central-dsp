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
        return {"external_id": _stable_id("MOCKCP", data)}

    async def register_contract(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"external_id": _stable_id("MOCKCT", data)}

    async def register_creative(self, data: dict[str, Any]) -> dict[str, Any]:
        ext = _stable_id("MOCKCR", data)
        return {"external_id": ext, "erid": f"MOCK-{ext[-12:]}"}

    async def submit_stats(self, period: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"accepted": len(rows)}
