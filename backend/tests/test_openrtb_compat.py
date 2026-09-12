"""Audit R-04: OpenRTB-совместимость — PMP, native assets по запросу,
video MIME/protocol/duration.

Контрактные фикстуры bidder-go/testdata/openrtb/*.json общие с Go
(TestOpenRTB_Fixtures): оба биддера должны дать одинаковый вердикт —
причину несовместимости, набор native assets, выбранную сделку, MIME/Duration.
Неподдерживаемый запрос -> явный отказ с причиной в метрике, не невалидная ставка.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

import obs_metrics
import rtb_compat
from routers import rtb

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "bidder-go" / "testdata" / "openrtb"


def _load():
    files = sorted(FIXTURES.glob("*.json"))
    assert len(files) >= 10, f"fixtures missing in {FIXTURES}"
    return [(f.stem, json.loads(f.read_text(encoding="utf-8"))) for f in files]


def _evaluate(fx: dict):
    """Тот же порядок проверок, что в process_bid_request (и Go bidOnImp)."""
    imp, creative = fx["imp"], dict(fx["creative"])
    try:
        native_req = rtb_compat.parse_native_request(imp.get("native"))
    except rtb_compat.NativeRequestError:
        return rtb_compat.INCOMPAT_NATIVE_MALFORMED, None, None
    deal = rtb_compat.matched_deal(imp, fx.get("deal_ids"))
    if rtb_compat.private_auction_only(imp) and deal is None:
        return rtb_compat.INCOMPAT_PRIVATE_AUCTION, None, None
    if imp.get("video"):
        reason = rtb_compat.video_incompat(imp["video"], creative)
        if reason:
            return reason, None, deal
    assets = None
    if imp.get("native") and native_req is not None:
        assets = rtb_compat.build_native_assets(native_req, creative)
        if assets is None:
            return rtb_compat.INCOMPAT_NATIVE_ASSET, None, deal
    return None, assets, deal


@pytest.mark.parametrize("name,fx", _load(), ids=lambda v: v if isinstance(v, str) else "")
def test_fixture_matches_go(name, fx):
    expect = fx["expect"]
    incompat, assets, deal = _evaluate(fx)
    assert incompat == expect.get("incompat"), name
    if "native_assets" in expect:
        assert assets == expect["native_assets"], name
    creative = fx["creative"]
    if expect.get("video_mime"):
        assert rtb_compat.creative_video_mime(creative) == expect["video_mime"]
    if expect.get("vast_protocols") is not None:
        assert rtb_compat.creative_vast_protocols(creative) == expect["vast_protocols"]
    if expect.get("vast_duration"):
        assert rtb_compat.vast_duration(rtb_compat.creative_video_duration(creative)) == expect["vast_duration"]
    if "deal" in expect:
        assert deal == expect["deal"], name


def test_incompat_reasons_in_sync_with_go():
    """Список причин общий: метрика Go bidder_openrtb_incompat_total{reason}."""
    go_src = (FIXTURES.parents[1] / "internal" / "rtb" / "openrtb.go").read_text(encoding="utf-8")
    go_reasons = set(re.findall(r'Incompat\w+\s*=\s*"([a-z_]+)"', go_src))
    assert go_reasons == set(obs_metrics.OPENRTB_INCOMPAT_REASONS)
    for r in obs_metrics.OPENRTB_INCOMPAT_REASONS:
        obs_metrics.inc_openrtb_incompat(r)  # известные причины не бросают
    with pytest.raises(KeyError):
        obs_metrics.inc_openrtb_incompat("made_up")


def test_metric_rendered():
    obs_metrics.reset_for_tests()
    obs_metrics.inc_openrtb_incompat("video_mime")
    text = obs_metrics.render_prometheus()
    assert 'rtb_openrtb_incompat_total{reason="video_mime"} ' in text
    assert "# TYPE rtb_openrtb_incompat_total counter" in text


def test_vast_duration_hhmmss():
    assert rtb_compat.vast_duration(0) == "00:00:00"
    assert rtb_compat.vast_duration(15) == "00:00:15"
    assert rtb_compat.vast_duration(90) == "00:01:30"
    assert rtb_compat.vast_duration(3725) == "01:02:05"


def test_build_adm_video_uses_mime_and_duration():
    creative = {
        "type": "video", "name": "Ролик & Co", "creative_id": "c1", "click_url": "https://adv/x",
        "video_url": "https://cdn.example/spot.webm", "video_duration": 75,
    }
    adm = rtb._build_adm(creative, "https://api.example", "tok")
    assert '<Duration>00:01:15</Duration>' in adm
    assert 'type="video/webm"' in adm
    assert "<AdTitle>Ролик &amp; Co</AdTitle>" in adm


def test_build_adm_native_uses_requested_assets():
    creative = {
        "type": "native", "name": "n", "creative_id": "c2", "click_url": "https://adv/x",
        "native_title": "T", "native_description": "D", "asset_url": "https://cdn/a.jpg",
        "_native_assets": [{"id": 7, "title": {"text": "T"}}],
    }
    adm = json.loads(rtb._build_adm(creative, "https://api.example", "tok"))
    assert adm["native"]["assets"] == [{"id": 7, "title": {"text": "T"}}]
    # без _native_assets — legacy набор 1..5
    creative.pop("_native_assets")
    adm = json.loads(rtb._build_adm(creative, "https://api.example", "tok"))
    assert [a["id"] for a in adm["native"]["assets"]] == [1, 2, 3, 4, 5]


def test_creative_fits_imp_video_counts_metric():
    before = obs_metrics.snapshot()["openrtb_incompat"]["video_mime"]
    creative = {"type": "video", "video_url": "https://cdn/x.mp4", "click_url": "https://adv/l",
                "video_duration": 10, "width": None, "height": None}
    assert rtb._creative_fits_imp(creative, {"video": {"mimes": ["video/webm"]}}, "video") is False
    assert obs_metrics.snapshot()["openrtb_incompat"]["video_mime"] == before + 1
    assert rtb._creative_fits_imp(creative, {"video": {"mimes": ["video/mp4"], "maxduration": 30}}, "video") is True
