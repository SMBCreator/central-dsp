"""B13c (ord_spec §7): сводная площадка ОРС — ensure_platform без БД и сети.

Fake-соединение имитирует counterparties/platform_settings, fake-адаптер
записывает вызовы. Проверяем порядок (площадка → организация с владельцем),
идемпотентность по ключу и ошибки данных (нет ОРС / нет URL).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ord import platform as pl
from ord.adapter import MOCK_PROVIDER, OrdDataError


class FakeConn:
    def __init__(self, self_org=None, ord_ozon=None):
        self.self_org = self_org
        self.settings = {}
        if ord_ozon is not None:
            self.settings["ord_ozon"] = ord_ozon

    async def fetchrow(self, sql, *args):
        assert "FROM counterparties WHERE is_self" in sql
        return self.self_org

    async def fetchval(self, sql, *args):
        if "key = 'ord_ozon'" in sql:
            return self.settings.get("ord_ozon")
        if "WHERE key = $1" in sql:
            return self.settings.get(args[0])
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        assert "INSERT INTO platform_settings" in sql
        self.settings[args[0]] = json.loads(args[1])


class FakeAdapter:
    provider = "ozon"
    _base = "https://api-ord.ozon.ru"

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def register_platform(self, data):
        self.calls.append(("platform", data))
        return {"external_id": data["external_id"]}

    async def register_counterparty(self, data):
        self.calls.append(("organisation", data))
        return {"external_id": data["external_id"]}


SELF = {"id": "11111111-1111-1111-1111-111111111111", "name": "ООО Централ", "inn": "7700000002",
        "legal_type": "ul", "role": "ors", "address": "Москва", "is_self": True,
        "rs_url": "https://central-dsp.ru", "ord_status": "registered", "ord_external_id": None}


def test_ensure_platform_registers_platform_then_owner_and_caches():
    conn = FakeConn(self_org=SELF, ord_ozon={"api_key": "k", "platform_url": "https://central-dsp.ru/"})
    ad = FakeAdapter()
    out = asyncio.run(pl.ensure_platform(conn, ad))
    assert out["status"] == "ok" and out["url"] == "https://central-dsp.ru/"
    assert [c[0] for c in ad.calls] == ["platform", "organisation"]
    platform, org = ad.calls[0][1], ad.calls[1][1]
    assert platform["external_id"] == pl.platform_external_id() and platform["platform_type"] == "site"
    assert org["is_self"] is True and org["platforms"][0]["is_owner"] is True
    assert org["platforms"][0]["external_id"] == pl.platform_external_id()
    assert pl.SETTINGS_KEY in conn.settings

    # Повтор с теми же provider/base/url — no-op; force пересылает.
    asyncio.run(pl.ensure_platform(conn, ad))
    assert len(ad.calls) == 2
    asyncio.run(pl.ensure_platform(conn, ad, force=True))
    assert len(ad.calls) == 4


def test_ensure_platform_reregisters_when_url_changes():
    conn = FakeConn(self_org=SELF, ord_ozon={"api_key": "k", "platform_url": "https://a.ru"})
    ad = FakeAdapter()
    asyncio.run(pl.ensure_platform(conn, ad))
    conn.settings["ord_ozon"] = {"api_key": "k", "platform_url": "https://b.ru"}
    out = asyncio.run(pl.ensure_platform(conn, ad))
    assert out["url"] == "https://b.ru" and len(ad.calls) == 4


def test_ensure_platform_falls_back_to_rs_url(monkeypatch):
    monkeypatch.delenv("ORD_PLATFORM_URL", raising=False)
    conn = FakeConn(self_org=SELF)
    ad = FakeAdapter()
    out = asyncio.run(pl.ensure_platform(conn, ad))
    assert out["url"] == "https://central-dsp.ru"


def test_ensure_platform_requires_self_org_and_url(monkeypatch):
    monkeypatch.delenv("ORD_PLATFORM_URL", raising=False)
    ad = FakeAdapter()
    with pytest.raises(OrdDataError, match="self_org_missing"):
        asyncio.run(pl.ensure_platform(FakeConn(self_org=None), ad))
    with pytest.raises(OrdDataError, match="self_org_unregistered"):
        asyncio.run(pl.ensure_platform(FakeConn(self_org={**SELF, "ord_status": "pending"}), ad))
    with pytest.raises(OrdDataError, match="platform_url_missing"):
        asyncio.run(pl.ensure_platform(FakeConn(self_org={**SELF, "rs_url": None}), ad))
    assert ad.calls == []


def test_ensure_platform_skips_in_mock_without_self_org(monkeypatch):
    """dev/тесты с mock-ОРД не обязаны заводить организацию-ОРС."""
    monkeypatch.delenv("ORD_PLATFORM_URL", raising=False)

    class Mock(FakeAdapter):
        provider = MOCK_PROVIDER

    out = asyncio.run(pl.ensure_platform(FakeConn(self_org=None), Mock()))
    assert out == {"status": "skipped", "reason": "mock_self_org_missing"}
    out = asyncio.run(pl.ensure_platform(FakeConn(self_org={**SELF, "rs_url": None}), Mock()))
    assert out == {"status": "skipped", "reason": "mock_platform_url_missing"}
    with pytest.raises(OrdDataError, match="self_org_missing"):
        asyncio.run(pl.ensure_platform(FakeConn(self_org=None), Mock(), force=True))


def test_org_payload_attaches_platform_only_for_self():
    platform = {"external_id": "central-dsp", "name": "Central DSP", "url": "https://central-dsp.ru"}
    assert "platforms" in pl.org_payload(SELF, platform)
    assert "platforms" not in pl.org_payload({**SELF, "is_self": False}, platform)
    assert "platforms" not in pl.org_payload(SELF, None)


def test_platform_status_lists_missing_pieces(monkeypatch):
    monkeypatch.delenv("ORD_PLATFORM_URL", raising=False)
    st = asyncio.run(pl.platform_status(FakeConn(self_org=None)))
    assert st["missing"] == ["self_org", "platform_url"] and st["registered"] is None
    st = asyncio.run(pl.platform_status(FakeConn(self_org={**SELF, "ord_status": "pending"})))
    assert st["missing"] == ["self_org_registration"] and st["self_org"]["ord_status"] == "pending"
