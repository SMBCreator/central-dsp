"""Audit R-05: трекеры во всех форматах, экранирование по контексту, отказ от
небезопасных URL и невалидного VAST, /click без open redirect, video start —
не viewable. Юнит-тесты без БД; зеркало — bidder-go/internal/rtb/adm_r05_test.go.
"""
from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from urllib.parse import quote

import pytest
from pydantic import ValidationError

import obs_metrics
import rtb_compat
from routers import rtb as rtb_module
from routers.rtb import _build_adm, _creative_fits_imp, click, video_event
from schemas import CreativeIn, CreativePatch
from tokens import sign_event_token

BASE = "https://dsp.example"
BID = "9d3c2f1e-1111-2222-3333-444455556666"
TOKEN = sign_event_token(BID)
CLICK = f"{BASE}/api/rtb/click?bid={TOKEN}"


def _creative(typ="banner", **over):
    base = {
        "id": "11111111-2222-3333-4444-555555555555", "creative_id": "11111111-2222-3333-4444-555555555555",
        "type": typ, "name": "Осень", "width": 300, "height": 250,
        "asset_url": "https://cdn.example/b.png?a=1&b=2", "click_url": "https://adv.example/l?utm=1",
        "html_snippet": None, "vast_xml": None, "video_url": "https://cdn.example/v.mp4", "video_duration": 15,
        "native_title": "Заголовок", "native_description": "Текст", "native_icon_url": "https://cdn.example/i.png",
        "imp_tracker_url": "https://audit.example/imp?a=1&b=2",
        "click_tracker_url": "https://audit.example/click?x=1&y=${AUCTION_ID}",
        "erid": "2VtzqxABCDE", "adv_name": 'ООО "Ромашка"', "adv_inn": "7700000001",
    }
    base.update(over)
    return base


# --- safe_url / adm_incompat ---------------------------------------------------

class TestSafeURL:
    @pytest.mark.parametrize("u", [
        "https://adv.example/l?utm=1&x=2", "http://a.b/c",
        "https://ssp.example/p?price=${AUCTION_PRICE}&id={ID}",
    ])
    def test_accepts(self, u):
        assert rtb_compat.safe_url(u)

    @pytest.mark.parametrize("u", [
        "", None, "javascript:alert(1)", "data:text/html,x", "//evil.example/x", "ftp://a/b",
        "https://a.b/c d", 'https://a.b/c"onload=alert(1)', "https://a.b/c'x", "https://a.b/<x>",
        "https://a.b/\\x", "https:///nohost", "https://a.b/\n", "https://a.b/" + "x" * 2048,
    ])
    def test_rejects(self, u):
        assert not rtb_compat.safe_url(u)


class TestAdmIncompat:
    def test_valid_all_types(self):
        for typ in ("banner", "native", "push", "video"):
            assert rtb_compat.adm_incompat(_creative(typ)) is None

    def test_reasons(self):
        assert rtb_compat.adm_incompat(_creative(click_url="javascript:alert(1)")) == "adm_url_invalid"
        assert rtb_compat.adm_incompat(_creative(asset_url='https://c/x.png" onerror="alert(1)')) == "adm_url_invalid"
        assert rtb_compat.adm_incompat(_creative(asset_url=None, html_snippet="<div>x</div>")) is None
        assert rtb_compat.adm_incompat(_creative("native", native_icon_url="data:image/png;base64,AAAA")) == "adm_url_invalid"
        assert rtb_compat.adm_incompat(_creative("video", imp_tracker_url="https://x.y/p?a=1 b")) == "adm_url_invalid"
        assert rtb_compat.adm_incompat(_creative("video", vast_xml="<VAST><Ad><InLine><AdSystem>x</Ad></VAST>")) == "adm_vast_malformed"
        assert rtb_compat.adm_incompat(_creative("video", vast_xml='<VAST version="3.0"><Ad id="1"></Ad></VAST>')) == "adm_vast_malformed"
        assert rtb_compat.adm_incompat(_creative("video", vast_xml="<html><body>x</body></html>")) == "adm_vast_malformed"
        assert rtb_compat.adm_incompat(_creative("video", vast_xml='<VAST version="3.0"><Ad><InLine/></Ad></VAST>')) == "adm_vast_malformed"

    def test_creative_fits_imp_uses_it_and_counts_metric(self):
        before = obs_metrics.snapshot()["openrtb_incompat"]["adm_url_invalid"]
        assert _creative_fits_imp(_creative(click_url="javascript:x"), {"banner": {"w": 300, "h": 250}}, "banner") is False
        assert obs_metrics.snapshot()["openrtb_incompat"]["adm_url_invalid"] == before + 1
        assert _creative_fits_imp(_creative(), {"banner": {"w": 300, "h": 250}}, "banner") is True

    def test_reasons_in_sync_with_go_list(self):
        assert {"adm_url_invalid", "adm_vast_malformed"} <= set(obs_metrics.OPENRTB_INCOMPAT_REASONS)


# --- banner -------------------------------------------------------------------------

class TestBanner:
    def test_no_redirect_target_in_click_and_attribute_escaping(self):
        adm = _build_adm(_creative(), BASE, TOKEN)
        assert "&r=" not in adm and "r=https" not in adm
        assert f'href="{CLICK}"' in adm
        assert 'src="https://cdn.example/b.png?a=1&amp;b=2"' in adm
        assert 'src="https://audit.example/imp?a=1&amp;b=2"' in adm
        assert """onclick="(new Image()).src='https://audit.example/click?x=1\\x26y=${AUCTION_ID}'\"""" in adm
        assert "/api/rtb/view?bid=" in adm and "IntersectionObserver" in adm

    def test_js_string_escape_defense_in_depth(self):
        assert rtb_compat.js_string_escape("""https://a/b?q='x'&z="y"<s>""") == r"https://a/b?q=\x27x\x27\x26z=\x22y\x22\x3cs\x3e"

    def test_html5_macro_substituted(self):
        snippet = '<a href="${CLICK_URL}"><img src="https://cdn.example/x.png"></a><script>var u="%%CLICK_URL_ESC%%";</script>'
        adm = _build_adm(_creative(asset_url=None, html_snippet=snippet), BASE, TOKEN)
        assert f'href="{CLICK}"' in adm
        assert f'var u="{quote(CLICK, safe="")}"' in adm
        assert "CLICK_URL" not in adm
        assert "&beacon=1" not in adm

    def test_html5_without_macro_gets_capture_click_beacon(self):
        snippet = '<a href="https://adv.example/direct"><img src="https://cdn.example/x.png"></a>'
        adm = _build_adm(_creative(asset_url=None, html_snippet=snippet), BASE, TOKEN)
        assert snippet in adm
        assert 'addEventListener("click"' in adm and f"{CLICK}&beacon=1" in adm
        m = re.search(r'<div id="(cdsp_[^"]+)"><div id="(cdsp_[^"]+)_c">', adm)
        assert m and m.group(1) == m.group(2)
        assert f'getElementById("{m.group(1)}_c")' in adm


# --- native -----------------------------------------------------------------------

class TestNative:
    def test_link_and_eventtrackers(self):
        for typ in ("native", "push"):
            doc = json.loads(_build_adm(_creative(typ), BASE, TOKEN))["native"]
            assert doc["link"]["url"] == CLICK
            assert doc["link"]["clicktrackers"] == ["https://audit.example/click?x=1&y=${AUCTION_ID}"]
            assert doc["eventtrackers"] == [{"event": 2, "methods": [1], "url": f"{BASE}/api/rtb/view?bid={TOKEN}"}]
            assert doc["imptrackers"] == [f"{BASE}/api/rtb/imp?bid={TOKEN}", "https://audit.example/imp?a=1&b=2"]


# --- video -----------------------------------------------------------------------

CUSTOM_INLINE = """<?xml version="1.0" encoding="UTF-8"?>
<VAST version="3.0"><Ad id="ext-1"><InLine>
<AdSystem>Ext</AdSystem><AdTitle>t &amp; q</AdTitle>
<Impression id="third"><![CDATA[https://third/imp?a=1&b=2]]></Impression>
<Creatives><Creative><Linear skipoffset="00:00:05"><Duration>00:00:30</Duration>
<TrackingEvents><Tracking event="start"><![CDATA[https://third/start]]></Tracking></TrackingEvents>
<VideoClicks><ClickThrough><![CDATA[https://adv/landing]]></ClickThrough></VideoClicks>
<MediaFiles><MediaFile type="video/mp4" delivery="progressive" width="640" height="360"><![CDATA[https://cdn/v.mp4]]></MediaFile></MediaFiles>
</Linear></Creative></Creatives></InLine></Ad></VAST>"""


class TestVideo:
    def test_generated_vast_clickthrough_is_our_redirect(self):
        adm = _build_adm(_creative("video"), BASE, TOKEN)
        ET.fromstring(adm)
        assert f"<ClickThrough><![CDATA[{CLICK}]]></ClickThrough>" in adm
        assert f"<ClickTracking><![CDATA[{CLICK}&beacon=1]]></ClickTracking>" in adm
        assert "<ClickTracking><![CDATA[https://audit.example/click?x=1&y=${AUCTION_ID}]]></ClickTracking>" in adm
        assert f'<Tracking event="firstQuartile"><![CDATA[{BASE}/api/rtb/vevent?bid={TOKEN}&e=firstQuartile]]></Tracking>' in adm
        assert "&r=" not in adm

    def test_custom_inline_gets_all_trackers_and_keeps_content(self):
        adm = _build_adm(_creative("video", vast_xml=CUSTOM_INLINE), BASE, TOKEN)
        ET.fromstring(adm)
        for want in (
            f"<Impression><![CDATA[{BASE}/api/rtb/imp?bid={TOKEN}]]></Impression>",
            "<Impression><![CDATA[https://audit.example/imp?a=1&b=2]]></Impression>",
            f'<Tracking event="start"><![CDATA[{BASE}/api/rtb/vevent?bid={TOKEN}&e=start]]></Tracking>',
            f'<Tracking event="complete"><![CDATA[{BASE}/api/rtb/vevent?bid={TOKEN}&e=complete]]></Tracking>',
            f"<ClickTracking><![CDATA[{CLICK}&beacon=1]]></ClickTracking>",
            "<Erid>2VtzqxABCDE</Erid>",
            "<![CDATA[https://third/imp?a=1&b=2]]>", "<![CDATA[https://third/start]]>", "<![CDATA[https://adv/landing]]>",
            '<Linear skipoffset="00:00:05">', "<AdTitle>t &amp; q</AdTitle>",
        ):
            assert want in adm, want
        assert adm.count("<TrackingEvents>") == 1 and adm.count("<VideoClicks>") == 1
        assert adm.count("<ClickThrough>") == 1
        assert adm.index("third/imp") < adm.index(f"imp?bid={TOKEN}")

    def test_custom_wrapper_without_creatives_gets_linear_block(self):
        vast = ('<VAST version="3.0"><Ad id="w"><Wrapper><AdSystem>W</AdSystem><VASTAdTagURI><![CDATA[https://third/tag.xml]]></VASTAdTagURI>'
                "<Impression/></Wrapper></Ad></VAST>")
        adm = _build_adm(_creative("video", vast_xml=vast, imp_tracker_url=None, click_tracker_url=None), BASE, TOKEN)
        ET.fromstring(adm)
        assert f"<Impression/><Impression><![CDATA[{BASE}/api/rtb/imp?bid={TOKEN}]]></Impression>" in adm
        assert "<Creatives><Creative><Linear><TrackingEvents>" in adm
        assert f"<VideoClicks><ClickTracking><![CDATA[{CLICK}&beacon=1]]></ClickTracking></VideoClicks></Linear></Creative></Creatives></Wrapper>" in adm
        assert "<ClickThrough>" not in adm

    def test_pod_each_ad_tracked(self):
        one = '<Ad id="{i}" sequence="{i}"><InLine><AdSystem>x</AdSystem><Creatives><Creative><Linear><Duration>00:00:10</Duration></Linear></Creative></Creatives></InLine></Ad>'
        vast = '<VAST version="3.0">' + one.format(i=1) + one.format(i=2) + "</VAST>"
        adm = _build_adm(_creative("video", vast_xml=vast, imp_tracker_url=None, click_tracker_url=None), BASE, TOKEN)
        assert adm.count(f"imp?bid={TOKEN}") == 2 and adm.count("&e=complete") == 2

    def test_malformed_raises(self):
        with pytest.raises(ValueError):
            _build_adm(_creative("video", vast_xml="<VAST><Ad><InLine><AdSystem>x</Ad></VAST>"), BASE, TOKEN)

    def test_go_parity_fixture_identical(self):
        """Тот же вход даёт байт-в-байт тот же выход, что Go injectVastTrackers
        (см. adm_r05_test.go TestCustomVAST_InLine_GetsAllTrackers): проверяем
        детерминированные точки вставки — позицию Impression/Extensions/Tracking."""
        adm = _build_adm(_creative("video", vast_xml=CUSTOM_INLINE), BASE, TOKEN)
        assert adm.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<VAST version="3.0"><Ad id="ext-1"><InLine><Extensions>')
        assert "</Impression><Impression><![CDATA[https://dsp.example/api/rtb/imp" in adm
        assert '<TrackingEvents><Tracking event="start"><![CDATA[https://dsp.example' in adm
        assert "<VideoClicks><ClickTracking><![CDATA[https://dsp.example" in adm


# --- endpoints -------------------------------------------------------------------

def _row(creative_id="cr"):
    return {
        "imp_id": "1", "campaign_id": "c", "line_item_id": "li", "creative_id": creative_id,
        "supply_partner_id": "sp", "device_type": "desktop", "geo_country": "RU",
        "ad_type": "banner", "domain": "site.ru", "os": "linux", "browser": "chrome", "is_test": False,
    }


class _Pool:
    def __init__(self, click_url):
        self.click_url = click_url
        self.queries = []

    async def fetchval(self, sql, *args):
        self.queries.append((sql, args))
        return self.click_url


def _patch_common(monkeypatch, pool, bumps):
    @asynccontextmanager
    async def fake_once_txn(pool_, kind, bid):
        bumps.append(f"once:{kind}")
        yield object()

    async def fake_find_bid(pool_, bid):
        return _row()

    async def fake_log_event(conn, event_type, **kw):
        bumps.append(f"event:{event_type}")

    async def fake_bump_stats(conn, field, spend, *ids):
        bumps.append(field)

    async def fake_bump_domain(conn, row_, field="wins", spend_add=0.0):
        bumps.append(f"domain:{field}")

    async def fake_get_pool():
        return pool

    monkeypatch.setattr(rtb_module, "_once_txn", fake_once_txn)
    monkeypatch.setattr(rtb_module, "_find_bid", fake_find_bid)
    monkeypatch.setattr(rtb_module, "_log_event", fake_log_event)
    monkeypatch.setattr(rtb_module, "_bump_stats", fake_bump_stats)
    monkeypatch.setattr(rtb_module, "_bump_domain_stats", fake_bump_domain)
    monkeypatch.setattr(rtb_module, "get_pool", fake_get_pool)
    monkeypatch.setattr(rtb_module, "_cpc_of", lambda row: None)


class TestClickEndpoint:
    def test_redirects_only_to_creative_click_url_with_clid(self, monkeypatch):
        bumps: list[str] = []
        pool = _Pool("https://adv.example/l?utm=1#top")
        _patch_common(monkeypatch, pool, bumps)
        resp = asyncio.run(click(TOKEN))
        assert resp.status_code == 302
        assert resp.headers["location"] == f"https://adv.example/l?utm=1&cdsp_clid={BID}#top"
        assert "event:click" in bumps and "clicks" in bumps

    def test_legacy_r_param_is_ignored(self, monkeypatch):
        """Старые adm в кэше SSP всё ещё шлют &r=…: параметр не принимается,
        редирект идёт на click_url креатива — open redirect закрыт."""
        pool = _Pool("https://adv.example/landing")
        _patch_common(monkeypatch, pool, [])
        import inspect
        assert "r" not in inspect.signature(click).parameters
        resp = asyncio.run(click(TOKEN))
        assert resp.headers["location"].startswith("https://adv.example/landing?cdsp_clid=")

    def test_unsafe_click_url_in_db_yields_pixel_not_redirect(self, monkeypatch):
        pool = _Pool("javascript:alert(1)")
        _patch_common(monkeypatch, pool, [])
        resp = asyncio.run(click(TOKEN))
        assert resp.status_code == 200 and resp.media_type == "image/gif"

    def test_beacon_counts_click_without_redirect(self, monkeypatch):
        bumps: list[str] = []
        pool = _Pool("https://adv.example/l")
        _patch_common(monkeypatch, pool, bumps)
        resp = asyncio.run(click(TOKEN, beacon=1))
        assert resp.status_code == 200 and resp.media_type == "image/gif"
        assert "event:click" in bumps and "clicks" in bumps
        assert pool.queries == []  # цель не нужна — БД не трогаем


class TestVideoStartNotViewable:
    def test_start_bumps_video_starts_only(self, monkeypatch):
        bumps: list[str] = []
        _patch_common(monkeypatch, _Pool(""), bumps)
        asyncio.run(video_event(TOKEN, "start"))
        assert "video_starts" in bumps and "event:video_start" in bumps
        assert "viewable_imps" not in bumps and "event:viewable" not in bumps and "once:view" not in bumps


# --- schemas -----------------------------------------------------------------------

class TestCreativeSchemas:
    def _base(self, **over):
        d = {"campaign_id": "c", "name": "n", "type": "banner", "width": 300, "height": 250,
             "asset_url": "https://cdn.example/b.png", "click_url": "https://adv.example/l"}
        d.update(over)
        return d

    def test_valid(self):
        c = CreativeIn(**self._base(imp_tracker_url="  https://audit/imp?p=${AUCTION_PRICE} ", click_tracker_url=""))
        assert c.imp_tracker_url == "https://audit/imp?p=${AUCTION_PRICE}" and c.click_tracker_url is None

    @pytest.mark.parametrize("field,value", [
        ("click_url", "javascript:alert(1)"), ("asset_url", 'https://c/x.png" onerror="alert(1)'),
        ("native_icon_url", "data:image/png;base64,AAAA"), ("video_url", "ftp://x/y.mp4"),
        ("imp_tracker_url", "https://a.b/c d"), ("click_tracker_url", "//evil/x"),
    ])
    def test_rejects_unsafe_urls(self, field, value):
        with pytest.raises(ValidationError) as ei:
            CreativeIn(**self._base(**{field: value}))
        assert field in str(ei.value)

    def test_vast_xml_validated(self):
        ok = CreativeIn(**self._base(type="video", vast_xml=CUSTOM_INLINE))
        assert ok.vast_xml.startswith("<?xml")
        with pytest.raises(ValidationError):
            CreativeIn(**self._base(type="video", vast_xml="<VAST><Ad><InLine><AdSystem>x</Ad></VAST>"))
        with pytest.raises(ValidationError):
            CreativeIn(**self._base(type="video", vast_xml="<html>not vast</html>"))
        with pytest.raises(ValidationError):
            CreativeIn(**self._base(type="video", vast_xml="<VAST" + "x" * 70_000 + ">"))

    def test_patch_uses_same_validators(self):
        with pytest.raises(ValidationError):
            CreativePatch(click_url="javascript:alert(1)")
        with pytest.raises(ValidationError):
            CreativePatch(vast_xml="<not-vast/>")
        assert CreativePatch(click_url="https://ok.example/").click_url == "https://ok.example/"
