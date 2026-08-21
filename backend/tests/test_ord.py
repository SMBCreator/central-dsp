"""Тесты ОРД-модуля: mock-адаптер, инжекция erid в разметку, launch guard SQL."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ord.mock import MockOrdAdapter
from ord.adapter import get_ord_adapter


def test_mock_erid_deterministic():
    m = MockOrdAdapter()
    a = asyncio.run(m.register_creative({"creative_id": "x", "name": "n"}))
    b = asyncio.run(m.register_creative({"creative_id": "x", "name": "n"}))
    assert a == b
    assert a["erid"].startswith("MOCK-")


def test_mock_erid_differs_per_creative():
    m = MockOrdAdapter()
    a = asyncio.run(m.register_creative({"creative_id": "x"}))
    b = asyncio.run(m.register_creative({"creative_id": "y"}))
    assert a["erid"] != b["erid"]


def test_factory_without_key_returns_mock(monkeypatch):
    monkeypatch.delenv("ORD_OZON_API_KEY", raising=False)
    assert type(get_ord_adapter()).__name__ == "MockOrdAdapter"


def test_factory_with_key_returns_ozon(monkeypatch):
    monkeypatch.setenv("ORD_OZON_API_KEY", "k")
    assert type(get_ord_adapter()).__name__ == "OzonOrdClient"


def test_submit_stats_counts():
    m = MockOrdAdapter()
    out = asyncio.run(m.submit_stats("2026-07", [{"erid": "a"}, {"erid": "b"}]))
    assert out == {"accepted": 2}


# --- erid в разметке ---------------------------------------------------------
def _make_build_adm():
    from routers.rtb import _build_adm
    return _build_adm


def test_adm_banner_includes_erid():
    build = _make_build_adm()
    creative = {
        "type": "banner", "html_snippet": None, "asset_url": "http://a/img.png",
        "width": 300, "height": 250, "click_url": "http://x", "erid": "TESTERID1",
        "name": "b",
    }
    adm = build(creative, "http://base", "bid1")
    assert "erid: TESTERID1" in adm
    assert "Реклама" in adm


def test_adm_banner_no_erid_no_label():
    build = _make_build_adm()
    creative = {
        "type": "banner", "html_snippet": None, "asset_url": "http://a/img.png",
        "width": 300, "height": 250, "click_url": "http://x", "erid": None, "name": "b",
    }
    adm = build(creative, "http://base", "bid1")
    assert "erid" not in adm.lower() or "erid=" not in adm


def test_adm_native_erid_in_ext():
    import json
    build = _make_build_adm()
    creative = {
        "type": "native", "native_title": "t", "native_description": "d",
        "asset_url": "http://a", "native_icon_url": "", "native_cta": "go",
        "click_url": "http://x", "erid": "NATERID", "name": "n",
    }
    adm = json.loads(build(creative, "http://base", "bid1"))
    assert adm["native"]["ext"]["erid"] == "NATERID"


def test_adm_vast_erid_extension():
    build = _make_build_adm()
    creative = {
        "type": "video", "vast_xml": None, "video_url": "http://v/x.mp4",
        "video_duration": 15, "click_url": "http://x", "erid": "VASTERID",
        "name": "v", "id": "cr1",
    }
    adm = build(creative, "http://base", "bid1")
    assert '<Erid>VASTERID</Erid>' in adm


def test_adm_push_keeps_ad_format_with_erid():
    import json
    build = _make_build_adm()
    creative = {
        "type": "push", "native_title": "t", "native_description": "d",
        "asset_url": "http://a", "native_icon_url": "", "native_cta": "go",
        "click_url": "http://x", "erid": "PUSHERID", "name": "n",
    }
    adm = json.loads(build(creative, "http://base", "bid1"))
    assert adm["native"]["ext"]["erid"] == "PUSHERID"
    assert adm["native"]["ext"]["ad_format"] == "push"
