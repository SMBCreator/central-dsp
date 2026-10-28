"""Макросы ${CDSP_*} в ссылке перехода и пикселях (ТЗ trackers+macros, Фаза 1).

Без БД. Общие векторы лежат рядом с Go (bidder-go/internal/macros/testdata) и
читаются обоими языками; каталог — backend/url_macros.json.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import routers.creatives as crs  # noqa: E402
import url_macros  # noqa: E402
from auth import current_user  # noqa: E402
from routers import rtb as rtb_module  # noqa: E402
from routers.rtb import _build_adm, click  # noqa: E402
from schemas import CreativeBatchCommon, CreativeIn, CreativePatch  # noqa: E402
from tokens import sign_event_token  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "bidder-go" / "internal" / "macros" / "testdata" / "macro_cases.json"
GO_MACROS = ROOT / "bidder-go" / "internal" / "macros" / "macros.go"

BASE = "https://dsp.example"
BID = "9d3c2f1e-1111-2222-3333-444455556666"
TOKEN = sign_event_token(BID)


# --- общие векторы и каталог ----------------------------------------------------------

def _cases() -> list[dict]:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_shared_vectors_count():
    assert len(_cases()) >= 60


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_shared_vectors(case):
    assert url_macros.expand(case["template"], case["values"]) == case["want"]


class TestCatalog:
    def test_entries_are_well_formed(self):
        groups = {"ids", "inventory", "geo", "device", "ad", "time"}
        for e in url_macros.CATALOG:
            assert set(e) == {"key", "macro", "group", "label", "param", "example", "description"}
            assert e["macro"] == "${CDSP_" + e["key"] + "}"
            assert e["group"] in groups
            assert re.fullmatch(r"[A-Z0-9_]+", e["key"])
            assert re.fullmatch(r"[a-z0-9_]+", e["param"])
            assert all(e[f] for f in ("label", "example", "description"))
        assert len({e["key"] for e in url_macros.CATALOG}) == len(url_macros.CATALOG)
        assert len({e["param"] for e in url_macros.CATALOG}) == len(url_macros.CATALOG)
        assert {e["group"] for e in url_macros.CATALOG} == groups

    def test_forbidden_macros_absent(self):
        """Решение M3: IP, идентификаторы устройства, координаты, название SSP, цена."""
        forbidden = {"IP", "IFA", "IDFA", "GAID", "USER_ID", "BUYERUID", "LAT", "LON", "GEO_LAT", "GEO_LON",
                     "SSP_NAME", "PRICE", "CLEARING_PRICE", "AUCTION_PRICE"}
        assert forbidden.isdisjoint(url_macros.KEYS)

    def test_go_table_matches_catalog(self):
        """Порядок и имена в Go-таблице Names — как в каталоге (Go проверяет то же со своей стороны)."""
        src = GO_MACROS.read_text(encoding="utf-8")
        block = src.split("var Names = [numKeys]string{", 1)[1].split("}", 1)[0]
        assert tuple(re.findall(r'"([A-Z0-9_]+)"', block)) == url_macros.KEYS


# --- поиск, подсказки, валидация -------------------------------------------------------

class TestFindAndValidate:
    def test_find_macros_known_only_unique_in_order(self):
        url = "https://a/?c=${CDSP_COUNTRY}&x=${CDSP_COUNTY}&c2=${CDSP_COUNTRY}&s=${CDSP_SITE_ID}&p=${AUCTION_PRICE}"
        assert url_macros.find_macros(url) == ["COUNTRY", "SITE_ID"]
        assert url_macros.find_macros(None) == [] and url_macros.find_macros("") == []

    def test_unknown_macros(self):
        assert url_macros.unknown_macros("https://a/?c=${CDSP_COUNTY}") == ["${CDSP_COUNTY}"]
        assert url_macros.unknown_macros("https://a/?c=${cdsp_country}") == ["${cdsp_country}"]
        assert url_macros.unknown_macros("https://a/?c=${CDSP_}") == ["${CDSP_}"]
        assert url_macros.unknown_macros("https://a/?c=${CDSP_COUNTRY}") == []
        assert url_macros.unknown_macros("https://a/?p=${AUCTION_PRICE}&u=${CLICK_URL}&c={country}") == []
        assert url_macros.unknown_macros(None) == []

    def test_suggest_closest(self):
        assert url_macros.suggest("${CDSP_COUNTY}")[0] == "${CDSP_COUNTRY}"
        assert url_macros.suggest("${cdsp_country}")[0] == "${CDSP_COUNTRY}"
        assert url_macros.suggest("${CDSP_ZZZZZZZZ}") == []

    def test_validate_message_has_hint(self):
        msg = url_macros.validate_macros("https://a/?c=${CDSP_COUNTY}")
        assert msg and "Неизвестный макрос ${CDSP_COUNTY}" in msg and "${CDSP_COUNTRY}" in msg
        assert url_macros.validate_macros("https://a/?c=${CDSP_COUNTRY}") is None

    def test_no_panic_on_garbage(self):
        for s in ["\ud800${CDSP_COUNTRY}", "${CDSP_", "}}${CDSP_COUNTRY}{{", "${CDSP_" * 3000,
                  "${CDSP_COUNTRY}" * 2000, "a" * 10_000 + "${CDSP_CITY}", "%", "${CDSP_COUNTRY}%zz"]:
            url_macros.find_macros(s)
            url_macros.unknown_macros(s)
            url_macros.expand(s, {"COUNTRY": "K\ud800Z", "CITY": "я" * 5000})


# --- сохранение креатива: P4 -----------------------------------------------------------

def _base(**over):
    d = {"campaign_id": "c", "name": "n", "type": "banner", "width": 300, "height": 250,
         "asset_url": "https://cdn.example/b.png", "click_url": "https://adv.example/l"}
    d.update(over)
    return d


class TestSaveValidation:
    @pytest.mark.parametrize("field", ["click_url", "imp_tracker_url", "click_tracker_url"])
    def test_typo_rejected_with_hint(self, field):
        """P4: ${CDSP_COUNTY} при сохранении — 422 с подсказкой ${CDSP_COUNTRY}."""
        with pytest.raises(ValidationError) as ei:
            CreativeIn(**_base(**{field: "https://l.example/?c=${CDSP_COUNTY}"}))
        text = str(ei.value)
        assert field in text and "${CDSP_COUNTRY}" in text

    def test_valid_macros_and_ssp_macros_pass(self):
        c = CreativeIn(**_base(
            click_url="https://l.example/?c=${CDSP_COUNTRY}&city=${CDSP_CITY}&u=${CLICK_URL}",
            imp_tracker_url="https://t.example/i?p=${AUCTION_PRICE}&cb=${CDSP_CACHEBUSTER}",
            click_tracker_url="https://t.example/c?s=${CDSP_SITE_ID}&id=${AUCTION_ID}",
        ))
        assert "${CDSP_COUNTRY}" in c.click_url and "${AUCTION_PRICE}" in c.imp_tracker_url

    def test_creative_without_macros_unchanged(self):
        c = CreativeIn(**_base(imp_tracker_url="https://t.example/i?x=1", click_tracker_url=""))
        assert c.imp_tracker_url == "https://t.example/i?x=1" and c.click_tracker_url is None

    def test_wrong_case_rejected(self):
        with pytest.raises(ValidationError):
            CreativeIn(**_base(click_url="https://l.example/?c=${cdsp_country}"))

    def test_patch_validates_only_provided_fields(self):
        assert CreativePatch(name="x").click_url is None
        with pytest.raises(ValidationError):
            CreativePatch(imp_tracker_url="https://t.example/?c=${CDSP_COUNTY}")
        assert CreativePatch(imp_tracker_url="https://t.example/?c=${CDSP_COUNTRY}").imp_tracker_url

    def test_batch_common_validates(self):
        with pytest.raises(ValidationError):
            CreativeBatchCommon(click_url="https://l.example/?c=${CDSP_COUNTY}")
        assert CreativeBatchCommon(click_url="https://l.example/?c=${CDSP_COUNTRY}").click_url

    def test_expanded_url_length_not_checked_on_save(self):
        """Лимит 2048 действует на шаблон; итог проверяет биддер на ставке."""
        c = CreativeIn(**_base(click_url="https://l.example/?c=${CDSP_CITY}"))
        assert c.click_url.endswith("${CDSP_CITY}")


# --- эндпоинт каталога -----------------------------------------------------------------

def _client(user: dict | None) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(crs.router)
    if user is not None:
        async def _fake_user():
            return user

        app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_macros_endpoint_requires_auth():
    async def scenario():
        async with _client(None) as c:
            assert (await c.get("/creatives/macros")).status_code == 401

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["admin", "manager", "viewer", "analyst"])
def test_macros_endpoint_returns_catalog_for_any_role(role):
    async def scenario():
        async with _client({"id": "u1", "role": role, "email": "u@x"}) as c:
            r = await c.get("/creatives/macros")
            assert r.status_code == 200
            assert r.json() == url_macros.CATALOG

    asyncio.run(scenario())


# --- разметка: P1, P5, P6, P7 ----------------------------------------------------------

def _creative(typ="banner", **over):
    c = {
        "id": "cr1", "creative_id": "cr1", "campaign_id": "7", "li_id": "9", "type": typ, "name": "Осень",
        "width": 300, "height": 250, "asset_url": "https://cdn.example/b.png",
        "click_url": "https://adv.example/l", "html_snippet": None, "vast_xml": None,
        "video_url": "https://cdn.example/v.mp4", "video_duration": 15,
        "native_title": "Заголовок", "native_description": "Текст", "native_icon_url": "https://cdn.example/i.png",
        "imp_tracker_url": None, "click_tracker_url": None, "erid": "2VtzqxABCDE",
    }
    c.update(over)
    return c


REQ = {
    "id": "auc-1",
    "imp": [{"id": "1", "tagid": "top_300x250"}],
    "device": {"ua": "Mozilla/5.0", "geo": {"country": "KAZ", "region": "ALA", "city": "Алматы"}},
    "site": {"id": "12345", "domain": "pub.example"},
}


def _ctx(creative, **over):
    ctx = url_macros.build_context(
        req=REQ, imp=REQ["imp"][0], creative=creative, partner_id="5", bid_id=BID,
        device_type="desktop", os_name="linux", browser="chrome", now=1793124723, cachebuster=48201733,
    )
    ctx.update(over)
    return ctx


class TestContext:
    def test_values_from_request(self):
        ctx = _ctx(_creative())
        assert ctx["COUNTRY"] == "KZ" and ctx["CITY"] == "Алматы" and ctx["REGION"] == "ALA"
        assert ctx["SITE_ID"] == "12345" and ctx["PLACEMENT_ID"] == "top_300x250" and ctx["DOMAIN"] == "pub.example"
        assert ctx["SIZE"] == "300x250" and ctx["AD_FORMAT"] == "banner" and ctx["SSP_ID"] == "5"
        assert ctx["TIMESTAMP"] == "1793124723" and ctx["CACHEBUSTER"] == "48201733"
        assert set(ctx) == set(url_macros.KEYS)

    def test_app_only_request(self):
        req = {"id": "a", "imp": [{"id": "1"}], "app": {"id": "app-1", "bundle": "Com.Example.App"}, "device": {}}
        ctx = url_macros.build_context(req=req, imp=req["imp"][0], creative=_creative(), partner_id=1, bid_id=BID,
                                       device_type="mobile", os_name="android", browser="chrome")
        assert ctx["SITE_ID"] == "app-1" and ctx["BUNDLE"] == "com.example.app" and ctx["DOMAIN"] == "com.example.app"

    @pytest.mark.parametrize("ct,want", [(0, "unknown"), (1, "ethernet"), (2, "wifi"), (3, "cellular"),
                                         (7, "cellular"), (8, "unknown"), ("x", "unknown")])
    def test_connection(self, ct, want):
        req = {**REQ, "device": {**REQ["device"], "connectiontype": ct}}
        ctx = url_macros.build_context(req=req, imp=REQ["imp"][0], creative=_creative(), partner_id=1, bid_id=BID,
                                       device_type="d", os_name="o", browser="b")
        assert ctx["CONNECTION"] == want


class TestBuildAdmMacros:
    def test_p1_banner_imp_pixel_expanded(self):
        c = _creative(imp_tracker_url="https://t.example/i?c=${CDSP_COUNTRY}&s=${CDSP_SITE_ID}")
        adm = _build_adm(c, BASE, TOKEN, _ctx(c))
        assert 'src="https://t.example/i?c=KZ&amp;s=12345"' in adm and "CDSP_" not in adm

    def test_p3_no_macros_identical_with_and_without_context(self):
        for typ in ("banner", "native", "push", "video"):
            c = _creative(typ, imp_tracker_url="https://t.example/i?a=1&b=2",
                          click_tracker_url="https://t.example/c?y=${AUCTION_ID}")
            assert _build_adm(c, BASE, TOKEN, _ctx(c)) == _build_adm(c, BASE, TOKEN), typ

    def test_p5_ssp_macro_untouched(self):
        c = _creative(imp_tracker_url="https://t.example/i?p=${AUCTION_PRICE}&c=${CDSP_COUNTRY}")
        assert "p=${AUCTION_PRICE}&amp;c=KZ" in _build_adm(c, BASE, TOKEN, _ctx(c))

    def test_p6_too_long_pixel_dropped_bid_remains(self):
        c = _creative(imp_tracker_url="https://t.example/i?c=${CDSP_CITY}")
        adm = _build_adm(c, BASE, TOKEN, _ctx(c, CITY="a" * 3000))
        assert "t.example" not in adm and "/api/rtb/imp?bid=" in adm

    def test_unsafe_expansion_dropped(self):
        c = _creative(imp_tracker_url="${CDSP_COUNTRY}://t.example/i")
        assert "t.example" not in _build_adm(c, BASE, TOKEN, _ctx(c))

    def test_p7_native_push_video(self):
        for typ in ("native", "push"):
            c = _creative(typ, imp_tracker_url="https://t.example/i?c=${CDSP_COUNTRY}",
                          click_tracker_url="https://t.example/c?s=${CDSP_SITE_ID}")
            n = json.loads(_build_adm(c, BASE, TOKEN, _ctx(c)))["native"]
            assert "https://t.example/i?c=KZ" in n["imptrackers"], typ
            assert n["link"]["clicktrackers"] == ["https://t.example/c?s=12345"], typ
        c = _creative("video", imp_tracker_url="https://t.example/i?c=${CDSP_COUNTRY}",
                      click_tracker_url="https://t.example/c?s=${CDSP_SITE_ID}")
        adm = _build_adm(c, BASE, TOKEN, _ctx(c))
        assert "<Impression><![CDATA[https://t.example/i?c=KZ]]></Impression>" in adm
        assert "<ClickTracking><![CDATA[https://t.example/c?s=12345]]></ClickTracking>" in adm

    def test_banner_click_tracker_expanded_in_onclick(self):
        c = _creative(click_tracker_url="https://t.example/c?s=${CDSP_SITE_ID}")
        assert "(new Image()).src='https://t.example/c?s=12345'" in _build_adm(c, BASE, TOKEN, _ctx(c))


# --- клик: P2 --------------------------------------------------------------------------

class _Pool:
    def __init__(self, click_url):
        self.click_url = click_url
        self.queries = []

    async def fetchval(self, sql, *args):
        self.queries.append((sql, args))
        return self.click_url


def _patch_click(monkeypatch, pool, row):
    @asynccontextmanager
    async def fake_once_txn(pool_, kind, bid):
        yield object()

    async def fake_find_bid(pool_, bid):
        return row

    async def noop(*a, **kw):
        return None

    async def fake_get_pool():
        return pool

    monkeypatch.setattr(rtb_module, "_once_txn", fake_once_txn)
    monkeypatch.setattr(rtb_module, "_find_bid", fake_find_bid)
    monkeypatch.setattr(rtb_module, "_log_event", noop)
    monkeypatch.setattr(rtb_module, "_bump_stats", noop)
    monkeypatch.setattr(rtb_module, "_bump_domain_stats", noop)
    monkeypatch.setattr(rtb_module, "get_pool", fake_get_pool)
    monkeypatch.setattr(rtb_module, "_cpc_of", lambda r: None)


def _bidmeta(**over):
    row = {"imp_id": "1", "campaign_id": "c", "line_item_id": "li", "creative_id": "cr", "supply_partner_id": "sp",
           "device_type": "desktop", "geo_country": "KZ", "ad_type": "banner", "domain": "site.kz",
           "os": "linux", "browser": "chrome", "is_test": 0}
    row.update(over)
    return row


class TestClickRedirect:
    def test_p2_expanded_link_from_bidmeta_with_clid(self, monkeypatch):
        link = "https://l.example/?city=%D0%90%D0%BB%D0%BC%D0%B0%D1%82%D1%8B"
        pool = _Pool("https://l.example/?city=${CDSP_CITY}")
        _patch_click(monkeypatch, pool, _bidmeta(click_url=link))
        resp = asyncio.run(click(TOKEN))
        assert resp.status_code == 302
        assert resp.headers["location"] == f"{link}&cdsp_clid={BID}"
        assert pool.queries == []  # развёрнутая ссылка есть — БД не нужна

    def test_p3_without_field_falls_back_to_db(self, monkeypatch):
        pool = _Pool("https://adv.example/l?utm=1#top")
        _patch_click(monkeypatch, pool, _bidmeta())
        resp = asyncio.run(click(TOKEN))
        assert resp.headers["location"] == f"https://adv.example/l?utm=1&cdsp_clid={BID}#top"
        assert len(pool.queries) == 1

    def test_unsafe_bidmeta_link_yields_pixel(self, monkeypatch):
        _patch_click(monkeypatch, _Pool(""), _bidmeta(click_url="javascript:alert(1)"))
        resp = asyncio.run(click(TOKEN))
        assert resp.status_code == 200 and resp.media_type == "image/gif"

    def test_beacon_ignores_bidmeta_link(self, monkeypatch):
        pool = _Pool("")
        _patch_click(monkeypatch, pool, _bidmeta(click_url="https://l.example/x"))
        resp = asyncio.run(click(TOKEN, beacon=1))
        assert resp.status_code == 200 and resp.media_type == "image/gif" and pool.queries == []
