"""Viewability (Часть 3a): MRC-замер в разметке, событие view, viewable_imps в статистике.

Юнит-тесты без живой БД (как в остальных tests/*):
- adm баннера содержит скрипт-замер IntersectionObserver (MRC: 50% >=1 сек) и маячок /rtb/view
- нативный adm содержит eventtrackers с event=2 (viewable MRC 50%)
- эндпоинт /rtb/view идемпотентен через _once_txn (E-02) и пишет viewable_imps
- математика viewability = viewable/imps*100
"""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager

from routers import rtb as rtb_module
from routers.rtb import _build_adm, viewable_impression
from tokens import sign_event_token

BASE = "http://localhost:8000"
TOKEN = sign_event_token("bid-view-test")


def _banner_creative(**over):
    base = {
        "id": "cr-1", "type": "banner", "width": 240, "height": 400,
        "asset_url": "https://cdn.example.com/b.png", "click_url": "https://adv.example.com",
        "html_snippet": None, "erid": None,
    }
    base.update(over)
    return base


def _native_creative(**over):
    base = {
        "id": "cr-2", "type": "native", "native_title": "Заголовок",
        "native_description": "Текст", "asset_url": "https://cdn.example.com/n.png",
        "click_url": "https://adv.example.com", "native_cta": "Подробнее", "erid": None,
    }
    base.update(over)
    return base


class TestBannerViewabilityMarkup:
    def test_banner_adm_contains_view_beacon(self):
        adm = _build_adm(_banner_creative(), BASE, TOKEN)
        assert "/api/rtb/view?bid=" in adm

    def test_banner_adm_contains_intersection_observer(self):
        adm = _build_adm(_banner_creative(), BASE, TOKEN)
        assert "IntersectionObserver" in adm
        # MRC: порог 50% площади и 1000 мс непрерывной видимости
        assert "intersectionRatio>=0.5" in adm and "},1000)" in adm

    def test_banner_adm_has_wrapper_div_for_observer(self):
        adm = _build_adm(_banner_creative(), BASE, TOKEN)
        m = re.search(r'<div id="(cdsp_[^"]+)"', adm)
        assert m, "обёртка с id для IntersectionObserver обязательна"
        assert f'getElementById("{m.group(1)}")' in adm

    def test_html_snippet_banner_also_wrapped(self):
        adm = _build_adm(_banner_creative(html_snippet="<div>custom</div>"), BASE, TOKEN)
        assert "IntersectionObserver" in adm
        assert "<div>custom</div>" in adm

    def test_imp_pixel_still_present(self):
        adm = _build_adm(_banner_creative(), BASE, TOKEN)
        assert "/api/rtb/imp?bid=" in adm


class TestNativeViewabilityTracker:
    def test_native_has_eventtrackers_mrc50(self):
        adm = _build_adm(_native_creative(), BASE, TOKEN)
        payload = json.loads(adm)
        trackers = payload["native"]["eventtrackers"]
        assert any(t["event"] == 2 and 1 in t["methods"] and "/api/rtb/view" in t["url"] for t in trackers)

    def test_native_imptrackers_untouched(self):
        adm = _build_adm(_native_creative(), BASE, TOKEN)
        payload = json.loads(adm)
        assert any("/api/rtb/imp" in u for u in payload["native"]["imptrackers"])

    def test_push_gets_same_tracker(self):
        adm = _build_adm(_native_creative(type="push"), BASE, TOKEN)
        payload = json.loads(adm)
        assert payload["native"]["eventtrackers"][0]["event"] == 2


class TestViewEndpoint:
    def test_bad_token_returns_pixel_without_db(self, monkeypatch):
        async def boom():
            raise AssertionError("нет валидного токена — БД трогать нельзя")

        monkeypatch.setattr(rtb_module, "get_pool", boom)
        resp = asyncio.run(viewable_impression("not-a-token"))
        assert resp.media_type == "image/gif"

    def test_view_idempotent_and_bumps_once(self, monkeypatch):
        """Дедуп теперь в _once_txn (E-02): первая заявка отдаёт соединение,
        повтор — None, и обработчик ничего не пишет."""
        state = {"claims": 0}
        bumps: list[str] = []
        row = {
            "imp_id": "1", "campaign_id": "c", "line_item_id": "li", "creative_id": "cr",
            "supply_partner_id": "sp", "device_type": "desktop", "geo_country": "RU",
            "ad_type": "banner", "domain": "site.ru", "os": "linux", "browser": "chrome",
        }

        @asynccontextmanager
        async def fake_once_txn(pool, kind, bid):
            assert kind == "view"
            state["claims"] += 1
            yield object() if state["claims"] == 1 else None

        async def fake_bump_stats(pool, field, spend, *ids):
            bumps.append(field)

        async def fake_bump_domain(pool, row_, field="wins", spend_add=0.0):
            bumps.append(f"domain:{field}")

        async def fake_find_bid(pool, bid):
            return row

        async def fake_log_event(pool, event_type, **kw):
            bumps.append(f"event:{event_type}")

        async def fake_get_pool():
            return None

        monkeypatch.setattr(rtb_module, "_once_txn", fake_once_txn)
        monkeypatch.setattr(rtb_module, "_bump_stats", fake_bump_stats)
        monkeypatch.setattr(rtb_module, "_bump_domain_stats", fake_bump_domain)
        monkeypatch.setattr(rtb_module, "_find_bid", fake_find_bid)
        monkeypatch.setattr(rtb_module, "_log_event", fake_log_event)
        monkeypatch.setattr(rtb_module, "get_pool", fake_get_pool)

        r1 = asyncio.run(viewable_impression(TOKEN))
        r2 = asyncio.run(viewable_impression(TOKEN))
        assert r1.media_type == "image/gif" and r2.media_type == "image/gif"
        # Первый вызов записал событие и статистику, второй (дубль) — нет
        assert bumps.count("event:viewable") == 1
        assert bumps.count("viewable_imps") == 1
        assert bumps.count("domain:viewable_imps") == 1


class TestViewabilityMath:
    def test_viewability_percent(self):
        imps, viewable = 2000, 1240
        assert round(viewable / imps * 100, 1) == 62.0

    def test_zero_imps_no_division(self):
        imps, viewable = 0, 0
        v = round(viewable / imps * 100, 1) if imps else 0
        assert v == 0
