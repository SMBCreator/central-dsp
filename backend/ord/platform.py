"""Сводная площадка ОРС и «наша» организация (ord_spec.md §7, B13c).

Central DSP — оператор рекламной системы: в ОРД одна организация с isOpc=true
и одна сводная площадка `central-dsp` (тип «Сайт», URL рекламной системы),
владелец — эта организация. Статистика показов всегда ссылается на эту площадку.

ensure_platform() вызывается перед любой отправкой статистики (крон, акт) и из
POST /ord/platform/register: регистрирует площадку, затем пересылает нашу
организацию с platforms[] (isPlatformOwner) — так ОРД связывает их. Результат
кэшируется в platform_settings.ord_platform по ключу provider+base_url+url.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from .adapter import OrdDataError

REAL_PROVIDER = "ozon"

DEFAULT_PLATFORM_EXTERNAL_ID = "central-dsp"


def platform_external_id() -> str:
    """externalPlatformId сводной площадки (env читается при вызове — тесты его подменяют)."""
    return os.getenv("ORD_PLATFORM_EXTERNAL_ID", DEFAULT_PLATFORM_EXTERNAL_ID)


def platform_name() -> str:
    return os.getenv("ORD_PLATFORM_NAME", "Central DSP")


SETTINGS_KEY = "ord_platform"

SELF_ORG_MISSING = ("self_org_missing: не задана наша организация (ОРС). Отметьте контрагента "
                    "«Наша организация» на странице «Маркировка»")
SELF_ORG_UNREGISTERED = "self_org_unregistered: наша организация (ОРС) не зарегистрирована в ОРД"
PLATFORM_URL_MISSING = ("platform_url_missing: не задан URL рекламной системы для сводной площадки "
                        "(Настройки → ОРД или ORD_PLATFORM_URL)")


def org_payload(cp: dict[str, Any], platform: dict[str, Any] | None = None) -> dict[str, Any]:
    """counterparties row -> данные для adapter.register_counterparty().
    Единственная точка сборки: ручная регистрация и пересылка из ensure_platform()."""
    data = {
        "external_id": str(cp["id"]), "name": cp["name"], "inn": cp["inn"], "ogrn": cp.get("ogrn"),
        "legal_type": cp["legal_type"], "role": cp["role"], "address": cp.get("address"),
        "is_self": bool(cp.get("is_self")), "rs_url": cp.get("rs_url"),
    }
    if data["is_self"] and platform:
        data["platforms"] = [{
            "external_id": platform["external_id"], "name": platform["name"],
            "platform_type": "site", "url": platform["url"], "is_owner": True,
        }]
    return data


async def get_self_org(conn) -> dict[str, Any] | None:
    row = await conn.fetchrow("SELECT * FROM counterparties WHERE is_self LIMIT 1")
    return dict(row) if row else None


async def platform_url(conn) -> str:
    """URL рекламной системы: настройки ОРД (ord_ozon.platform_url) → env → rs_url нашей организации."""
    raw = await conn.fetchval("SELECT value FROM platform_settings WHERE key = 'ord_ozon'")
    if raw:
        cfg = raw if isinstance(raw, dict) else json.loads(raw)
        if cfg.get("platform_url"):
            return str(cfg["platform_url"]).strip()
    if os.getenv("ORD_PLATFORM_URL"):
        return os.environ["ORD_PLATFORM_URL"].strip()
    self_org = await get_self_org(conn)
    return str(self_org.get("rs_url") or "").strip() if self_org else ""


async def load_platform(conn) -> dict[str, Any] | None:
    raw = await conn.fetchval("SELECT value FROM platform_settings WHERE key = $1", SETTINGS_KEY)
    if not raw:
        return None
    return raw if isinstance(raw, dict) else json.loads(raw)


async def platform_status(conn) -> dict[str, Any]:
    """Для GET /ord/platform: что зарегистрировано и чего не хватает."""
    self_org = await get_self_org(conn)
    saved = await load_platform(conn)
    url = await platform_url(conn)
    missing: list[str] = []
    if not self_org:
        missing.append("self_org")
    elif self_org["ord_status"] != "registered":
        missing.append("self_org_registration")
    if not url:
        missing.append("platform_url")
    return {
        "external_id": platform_external_id(), "name": platform_name(), "url": url,
        "self_org": ({"id": str(self_org["id"]), "name": self_org["name"], "inn": self_org["inn"],
                      "ord_status": self_org["ord_status"]} if self_org else None),
        "registered": saved, "missing": missing,
    }


def _cache_key(adapter, url: str) -> str:
    return f"{adapter.provider}:{getattr(adapter, '_base', '')}:{platform_external_id()}:{url}"


async def ensure_platform(conn, adapter, *, force: bool = False) -> dict[str, Any]:
    """Регистрирует сводную площадку и привязывает её к нашей организации.

    Идемпотентно: повтор с тем же provider/base_url/url — no-op (force=True
    пересылает). Без нашей организации / URL — OrdDataError (4xx-класс: данные,
    не сеть). В mock-режиме без нашей организации фоновый вызов — пропуск:
    dev/тесты не обязаны заводить ОРС; force (ручная регистрация) — ошибка.
    """
    self_org = await get_self_org(conn)
    url = await platform_url(conn)
    problem = (SELF_ORG_MISSING if not self_org
               else SELF_ORG_UNREGISTERED if self_org["ord_status"] != "registered"
               else PLATFORM_URL_MISSING if not url else None)
    if problem:
        # Фоновые вызовы (крон/акт) без реального ОРД (mock/тестовые адаптеры) не
        # требуют ОРС и URL; явная регистрация из UI (force) всегда отвечает честно.
        if adapter.provider != REAL_PROVIDER and not force:
            return {"status": "skipped", "reason": f"{adapter.provider}_" + problem.split(":", 1)[0]}
        raise OrdDataError(problem)
    assert self_org is not None

    key = _cache_key(adapter, url)
    saved = await load_platform(conn)
    if saved and saved.get("key") == key and not force:
        return {"status": "ok", **saved}

    platform = {"external_id": platform_external_id(), "name": platform_name(), "url": url, "platform_type": "site",
                "owner_external_id": self_org.get("ord_external_id") or str(self_org["id"])}
    await adapter.register_platform(platform)
    # Владелец площадки задаётся со стороны организации (platforms[].isPlatformOwner).
    await adapter.register_counterparty(org_payload(self_org, platform))

    value = {"key": key, "external_id": platform_external_id(), "name": platform_name(), "url": url,
             "provider": adapter.provider, "self_org_id": str(self_org["id"]),
             "registered_at": datetime.now(timezone.utc).isoformat()}
    await conn.execute(
        """INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        SETTINGS_KEY, json.dumps(value),
    )
    return {"status": "ok", **value}
