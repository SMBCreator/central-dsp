"""OpenRTB 2.5 bid endpoint + win/impression/click event tracking.

Flow:
  POST /rtb/bid/{endpoint_key}  <- BidRequest from SSP, returns BidResponse or 204
  GET  /rtb/win                 <- win notice (nurl), records win + spend
  GET  /rtb/imp                 <- impression tracker (burl)
  GET  /rtb/click               <- click tracker
"""
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

logger = logging.getLogger("rtb")

import obs_metrics
import prefilter
import ssp_check
import ssp_tokens
from billing_core import bill_win
from business_day import business_date_str, even_pacing_budget
from audiences import check_audiences, store_clid_bridge
from shading import get_factor as shading_get_factor, should_shade
from cache import (
    bump_frequency,
    bump_frequency_multi,
    check_frequency_cap,
    check_qps,
    gate_and_reserve,
    get_redis,
    mark_once,
    reservation_from_bidmeta,
    reserve_budget,
    settle_win,
)
from db import get_pool
from money import CURRENCY, clamp_clearing_price, cpm_to_cost
from ord.service import feed_gate_sql
from stats_math import INVENTORY_DIMENSIONS
from tokens import sign_event_token, verify_event_token

router = APIRouter(prefix="/rtb", tags=["rtb"])

# Python RTB — reference-имплементация и dev/симулятор. Прод-трафик обслуживает
# Go-биддер (bidder-go/). Флаг выключает bid-эндпоинт в прод-топологии;
# win/imp/click-нотификации остаются активными для доигрывания in-flight бидов.
# Audit S-04: в production дефолт — выключено (забытая переменная не должна
# включать второй биддер); явное включение там же валит старт (startup_checks).


def _python_serving_enabled() -> bool:
    raw = os.getenv("RTB_PYTHON_SERVING")
    if raw is None or not raw.strip():
        return os.getenv("APP_ENV", "").strip().lower() not in {"production", "prod"}
    return raw.strip().lower() in {"1", "true", "yes"}


_RTB_PYTHON_SERVING = _python_serving_enabled()

DEVICE_TYPES = {1: "mobile", 2: "desktop", 3: "ctv", 4: "phone", 5: "tablet", 6: "connected", 7: "settopbox"}

_BROWSER_MARKERS = [
    ("yabrowser", "yandex"), ("edg", "edge"), ("opr", "opera"), ("opera", "opera"),
    ("samsungbrowser", "samsung"), ("firefox", "firefox"), ("chrome", "chrome"), ("safari", "safari"),
]


def _device_os(device: dict) -> str:
    """OS from the bid request: explicit device.os, else UA sniff."""
    os_val = (device.get("os") or "").strip().lower()
    if os_val:
        return os_val
    ua = (device.get("ua") or "").lower()
    for marker, name in (("android", "android"), ("iphone", "ios"), ("ipad", "ios"),
                         ("windows", "windows"), ("mac os", "macos"), ("linux", "linux")):
        if marker in ua:
            return name
    return "unknown"


def _device_browser(device: dict) -> str:
    """Browser family from the UA string."""
    ua = (device.get("ua") or "").lower()
    for marker, name in _BROWSER_MARKERS:
        if marker in ua:
            return name
    return "unknown"


def _base_url() -> str:
    for var in ("BETTER_AUTH_URL", "VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL", "V0_RUNTIME_URL"):
        val = os.environ.get(var)
        if val:
            return val if val.startswith("http") else f"https://{val}"
    return "http://localhost:3000"


def _imp_ad_type(imp: dict) -> str | None:
    # Push traffic (arbitrage/push SSPs) is signalled via ext; it rides on the
    # native object in OpenRTB, so detect it before the generic native branch.
    ext = imp.get("ext") or {}
    if ext.get("push") or ext.get("ad_format") == "push":
        return "push"
    if imp.get("banner"):
        return "banner"
    if imp.get("native"):
        return "native"
    if imp.get("video"):
        return "video"
    return None


CONNECTION_TYPES = {0: "unknown", 1: "ethernet", 2: "wifi", 3: "cellular", 4: "cellular", 5: "cellular", 6: "cellular"}


def _match_targeting(targeting: dict, req: dict) -> bool:
    """Match line item targeting JSON against an OpenRTB 2.5 BidRequest.

    Supported keys (all optional; empty = no restriction):
      geo_countries, geo_regions, geo_cities        - ISO country / region / city lists
      device_types                                  - mobile / desktop / ctv / tablet
      os, browsers, languages                       - device.os, device.ua match, device.language
      connection_types                              - wifi / cellular / ethernet
      iab_categories                                - site.cat / app.cat IAB-1..26 allow list
      iab_block                                     - IAB category block list
      domain_allowlist, domain_blocklist            - site.domain / app.bundle
      age_min, age_max, genders                     - user.yob, user.gender (M/F/O)
      hours (0-23 UTC list), days (0=Mon..6=Sun)    - dayparting
      ssp_allowlist                                 - list of supply partner IDs (checked by caller)
    """
    from datetime import datetime, timezone

    device = req.get("device") or {}
    geo = device.get("geo") or {}
    site = req.get("site") or req.get("app") or {}
    user = req.get("user") or {}

    # --- Geo ---
    countries = targeting.get("geo_countries") or []
    if countries and (geo.get("country") or "").upper() not in [c.upper() for c in countries]:
        return False
    regions = targeting.get("geo_regions") or []
    if regions and (geo.get("region") or "").lower() not in [r.lower() for r in regions]:
        return False
    cities = targeting.get("geo_cities") or []
    if cities and (geo.get("city") or "").lower() not in [c.lower() for c in cities]:
        return False

    # --- Device ---
    device_types = targeting.get("device_types") or []
    if device_types:
        dt = DEVICE_TYPES.get(device.get("devicetype", 0), "unknown")
        normalized = {"phone": "mobile"}.get(dt, dt)
        if normalized not in device_types and dt not in device_types:
            return False

    os_list = targeting.get("os") or []
    if os_list and (device.get("os") or "").lower() not in [o.lower() for o in os_list]:
        return False

    browsers = targeting.get("browsers") or []
    if browsers:
        ua = (device.get("ua") or "").lower()
        if not any(b.lower() in ua for b in browsers):
            return False

    languages = targeting.get("languages") or []
    if languages and (device.get("language") or "").lower() not in [l.lower() for l in languages]:
        return False

    conn_types = targeting.get("connection_types") or []
    if conn_types:
        ct = CONNECTION_TYPES.get(device.get("connectiontype", 0), "unknown")
        if ct not in conn_types:
            return False

    # --- Inventory ---
    site_cats = [c.upper() for c in (site.get("cat") or [])]
    iab_allow = targeting.get("iab_categories") or []
    if iab_allow and not any(c.upper() in site_cats for c in iab_allow):
        return False
    iab_block = targeting.get("iab_block") or []
    if iab_block and any(c.upper() in site_cats for c in iab_block):
        return False

    domain = (site.get("domain") or site.get("bundle") or "").lower()
    domain_blocklist = targeting.get("domain_blocklist") or []
    if domain and domain in [d.lower() for d in domain_blocklist]:
        return False
    domain_allowlist = targeting.get("domain_allowlist") or []
    if domain_allowlist and domain not in [d.lower() for d in domain_allowlist]:
        return False

    # --- Audience ---
    genders = targeting.get("genders") or []
    if genders and (user.get("gender") or "").upper() not in [g.upper() for g in genders]:
        return False
    yob = user.get("yob")
    age_min, age_max = targeting.get("age_min"), targeting.get("age_max")
    if (age_min or age_max) and yob:
        age = datetime.now(timezone.utc).year - int(yob)
        if age_min and age < int(age_min):
            return False
        if age_max and age > int(age_max):
            return False

    # --- Dayparting (UTC) ---
    now = datetime.now(timezone.utc)
    hours = targeting.get("hours") or []
    if hours and now.hour not in [int(h) for h in hours]:
        return False
    days = targeting.get("days") or []
    if days and now.weekday() not in [int(d) for d in days]:
        return False

    # --- Inventory environment: site (web) vs app ---
    traffic_types = targeting.get("traffic_types") or []
    if traffic_types:
        env = "app" if req.get("app") else "site"
        if env not in traffic_types:
            return False

    # --- Carrier / ISP ---
    carriers = targeting.get("carriers") or []
    if carriers:
        carrier = (device.get("carrier") or "").lower()
        if not any(c.lower() in carrier or carrier in c.lower() for c in carriers if carrier):
            return False
        if not carrier:
            return False

    # --- Minimum OS version (major.minor numeric compare) ---
    osv_min = targeting.get("os_version_min")
    if osv_min:
        osv = device.get("osv") or ""
        try:
            def _ver(v: str) -> tuple:
                return tuple(int(p) for p in str(v).split(".")[:2])
            if not osv or _ver(osv) < _ver(osv_min):
                return False
        except (ValueError, TypeError):
            return False

    # --- App bundle allow/block (separate from web domains) ---
    app = req.get("app") or {}
    bundle = (app.get("bundle") or "").lower()
    bundles_block = [b.lower() for b in (targeting.get("app_bundles_block") or [])]
    if bundle and bundle in bundles_block:
        return False
    bundles_allow = [b.lower() for b in (targeting.get("app_bundles_allow") or [])]
    if bundles_allow and bundle not in bundles_allow:
        return False

    # --- Publisher allow/block (site.publisher.id / app.publisher.id) ---
    pub_id = str(((req.get("site") or req.get("app") or {}).get("publisher") or {}).get("id") or "")
    pubs_block = [str(p) for p in (targeting.get("publisher_block") or [])]
    if pub_id and pub_id in pubs_block:
        return False
    pubs_allow = [str(p) for p in (targeting.get("publisher_allow") or [])]
    if pubs_allow and pub_id not in pubs_allow:
        return False

    # --- Contextual keywords (site/app/content keywords + page URL) ---
    kw_haystack = " ".join([
        (site.get("keywords") or ""),
        ((site.get("content") or {}).get("keywords") or ""),
        (site.get("page") or ""),
        ((site.get("content") or {}).get("title") or ""),
    ]).lower()
    kw_block = targeting.get("keywords_block") or []
    if kw_block and any(k.lower() in kw_haystack for k in kw_block):
        return False
    kw_allow = targeting.get("keywords_allow") or []
    if kw_allow and not any(k.lower() in kw_haystack for k in kw_allow):
        return False

    # --- IP prefix ranges (e.g. "192.168." or "2a00:") ---
    ip_ranges = targeting.get("ip_ranges") or []
    if ip_ranges:
        ip = device.get("ip") or device.get("ipv6") or ""
        if not any(ip.startswith(p) for p in ip_ranges):
            return False

    # --- Regulatory: skip COPPA / GDPR traffic when the campaign opts out ---
    regs = req.get("regs") or {}
    if targeting.get("block_coppa") and regs.get("coppa") == 1:
        return False
    if targeting.get("block_gdpr") and ((regs.get("ext") or {}).get("gdpr") == 1 or regs.get("gdpr") == 1):
        return False
    # Respect Do-Not-Track / LMT when the line item requires personal targeting
    if targeting.get("skip_dnt") and (device.get("dnt") == 1 or device.get("lmt") == 1):
        return False

    return True


def _match_imp_targeting(targeting: dict, imp: dict) -> bool:
    """Impression-slot level targeting: position, interstitial, rewarded,
    secure inventory, video parameters, PMP deal restrictions."""
    # Banner fold position: 1 = above the fold, 3 = below the fold
    positions = targeting.get("banner_positions") or []
    if positions:
        pos = (imp.get("banner") or imp.get("video") or {}).get("pos")
        if pos is None or int(pos) not in [int(p) for p in positions]:
            return False

    # Interstitial (imp.instl = 1)
    instl = int(imp.get("instl") or 0)
    if targeting.get("interstitial_only") and instl != 1:
        return False
    if targeting.get("exclude_interstitial") and instl == 1:
        return False

    # Rewarded inventory (OpenRTB 2.6 imp.rwdd, or ext.rewarded fallback)
    if targeting.get("rewarded_only"):
        rwdd = imp.get("rwdd") or (imp.get("ext") or {}).get("rewarded") or 0
        if int(rwdd) != 1:
            return False

    # HTTPS-only inventory
    if targeting.get("secure_only") and int(imp.get("secure") or 0) != 1:
        return False

    # Video slot constraints
    video = imp.get("video") or {}
    if video:
        v_min = targeting.get("video_min_duration")
        if v_min and video.get("maxduration") and int(video["maxduration"]) < int(v_min):
            return False
        if targeting.get("video_skippable_only") and int(video.get("skip") or 0) != 1:
            return False

    # PMP: when deal IDs are configured, bid only on matching private deals
    deal_ids = [str(d) for d in (targeting.get("deal_ids") or [])]
    if deal_ids:
        deals = ((imp.get("pmp") or {}).get("deals") or [])
        if not any(str(d.get("id")) in deal_ids for d in deals):
            return False

    return True


def _matched_deal(targeting: dict, imp: dict) -> str | None:
    """Return the first configured deal ID present on the impression, if any."""
    deal_ids = [str(d) for d in (targeting.get("deal_ids") or [])]
    if not deal_ids:
        return None
    for d in (imp.get("pmp") or {}).get("deals") or []:
        if str(d.get("id")) in deal_ids:
            return str(d.get("id"))
    return None


def _creative_fits_imp(creative: dict, imp: dict, ad_type: str) -> bool:
    # push creatives can also fill plain native inventory (push IS native in RTB terms);
    # a dedicated push imp (ext.push) accepts only push creatives.
    if ad_type == "native":
        if creative["type"] not in ("native", "push"):
            return False
    elif creative["type"] != ad_type:
        return False
    if ad_type == "banner":
        banner = imp.get("banner") or {}
        bw, bh = banner.get("w"), banner.get("h")
        if bw and bh and creative["width"] and creative["height"]:
            if int(creative["width"]) != int(bw) or int(creative["height"]) != int(bh):
                formats = banner.get("format") or []
                if not any(f.get("w") == creative["width"] and f.get("h") == creative["height"] for f in formats):
                    return False
    if ad_type == "video":
        video = imp.get("video") or {}
        maxdur = video.get("maxduration")
        if maxdur and creative.get("video_duration") and creative["video_duration"] > maxdur:
            return False
    return True


def _html_escape(s: str) -> str:
    out = str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return out.replace('"', "&quot;").replace("'", "&#39;")


def _build_ad_info_badge(creative: dict) -> str:
    # Рекламное меню (38-ФЗ): значок "Реклама (i)", по клику раскрываются erid и данные рекламодателя.
    erid = creative.get("erid") or ""
    adv_name = creative.get("adv_name") or ""
    if not erid and not adv_name:
        return ""
    uid = str(creative.get("creative_id") or "")[:8]
    rows = ""
    if erid:
        rows += "<div>erid: " + _html_escape(erid) + "</div>"
    if adv_name:
        rows += "<div>" + _html_escape(adv_name) + "</div>"
    if creative.get("adv_inn"):
        rows += "<div>ИНН: " + _html_escape(creative["adv_inn"]) + "</div>"
    if creative.get("adv_ogrn"):
        rows += "<div>ОГРН: " + _html_escape(creative["adv_ogrn"]) + "</div>"
    if creative.get("adv_address"):
        rows += "<div>" + _html_escape(creative["adv_address"]) + "</div>"
    toggle = (
        "var p=document.getElementById('cdsp-ap-" + uid + "');"
        "p.style.display=p.style.display==='none'?'block':'none';"
        "event.preventDefault();event.stopPropagation();"
    )
    return (
        '<span id="cdsp-ai-' + uid + '" style="position:absolute;top:2px;left:2px;z-index:9;font:10px/1.4 sans-serif">'
        '<span onclick="' + toggle + '" style="cursor:pointer;background:rgba(0,0,0,.55);color:#fff;padding:1px 5px;'
        'border-radius:2px;user-select:none">Реклама &#9432;</span>'
        '<span id="cdsp-ap-' + uid + '" style="display:none;position:absolute;top:16px;left:0;background:rgba(0,0,0,.85);'
        'color:#fff;padding:6px 8px;border-radius:3px;min-width:180px;max-width:260px;white-space:normal;word-break:break-word">'
        + rows + "</span></span>"
    )


def _build_vast_extensions(creative: dict, erid: str) -> str:
    # VAST Extensions: erid + сведения о рекламодателе (38-ФЗ).
    if not erid and not creative.get("adv_name"):
        return ""
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&apos;"))
    parts = ""
    if erid:
        parts += f'<Extension type="erid"><Erid>{esc(erid)}</Erid></Extension>'
    if creative.get("adv_name"):
        parts += ('<Extension type="AdvertiserInfo">'
                  f'<Name>{esc(creative.get("adv_name") or "")}</Name>'
                  f'<INN>{esc(creative.get("adv_inn") or "")}</INN>'
                  f'<OGRN>{esc(creative.get("adv_ogrn") or "")}</OGRN>'
                  f'<Address>{esc(creative.get("adv_address") or "")}</Address>'
                  "</Extension>")
    return f"<Extensions>{parts}</Extensions>"


def _build_adm(creative: dict, base: str, bid_id: str) -> str:
    # Сторонние трекеры (аудит): пиксель на показ добавляется в разметку,
    # клик-трекер дёргается из клиентского onclick / clicktrackers / VAST.
    third_imp = creative.get("imp_tracker_url") or ""
    third_imp_px = (
        f'<img src="{third_imp}" width="1" height="1" style="display:none" alt=""/>'
        if third_imp else ""
    )
    third_click = creative.get("click_tracker_url") or ""
    erid = creative.get("erid") or ""
    erid_label = _build_ad_info_badge(creative)
    click_url = f"{base}/api/rtb/click?bid={bid_id}&r={creative.get('click_url') or ''}"
    imp_pixel = f"{base}/api/rtb/imp?bid={bid_id}"
    view_url = f"{base}/api/rtb/view?bid={bid_id}"
    if creative["type"] == "banner":
        click_js = (
            f' onclick="(new Image()).src=\'{third_click}\'"' if third_click else ""
        )
        # MRC viewability: >=50% площади в зоне видимости непрерывно >=1 сек.
        # IntersectionObserver с фолбэком «страница видима» для старых webview.
        wrap_id = f"cdsp_{bid_id[:24]}"
        view_script = (
            f'<script>(function(){{var f=0,t=null,el=document.getElementById("{wrap_id}");'
            f'function fire(){{if(f)return;f=1;(new Image()).src="{view_url}";}}'
            f'if(el&&"IntersectionObserver"in window){{'
            f'var o=new IntersectionObserver(function(es){{es.forEach(function(e){{'
            f'if(e.intersectionRatio>=0.5){{if(!t)t=setTimeout(function(){{fire();o.disconnect();}},1000);}}'
            f'else if(t){{clearTimeout(t);t=null;}}}});}},{{threshold:[0,0.5]}});o.observe(el);}}'
            f'else{{setTimeout(fire,1000);}}}})();</script>'
        )
        if creative.get("html_snippet"):
            return (f'<div id="{wrap_id}">' + creative["html_snippet"] + erid_label + "</div>"
                    + f'<img src="{imp_pixel}" width="1" height="1" style="display:none"/>' + third_imp_px + view_script)
        return (
            f'<div id="{wrap_id}"><a href="{click_url}" target="_blank"{click_js}>'
            f'<img src="{creative.get("asset_url") or ""}" width="{creative.get("width")}" height="{creative.get("height")}" alt=""/></a>'
            f'{erid_label}</div><img src="{imp_pixel}" width="1" height="1" style="display:none"/>' + third_imp_px + view_script
        )
    if creative["type"] in ("native", "push"):
        # push over RTB is native markup: title + body + icon + main image.
        native = {
            "native": {
                "link": {"url": click_url,
                         **({"clicktrackers": [third_click]} if third_click else {})},
                "imptrackers": [imp_pixel] + ([third_imp] if third_imp else []),
                # OpenRTB Native 1.2: event=2 (viewable MRC 50%), method=1 (img-пиксель).
                # Площадка дёргает URL, когда карточка проходит порог видимости.
                "eventtrackers": [{"event": 2, "methods": [1], "url": view_url}],
                "assets": [
                    {"id": 1, "title": {"text": creative.get("native_title") or ""}},
                    {"id": 2, "data": {"type": 2, "value": creative.get("native_description") or ""}},
                    {"id": 3, "img": {"type": 3, "url": creative.get("asset_url") or ""}},
                    {"id": 4, "img": {"type": 1, "url": creative.get("native_icon_url") or ""}},
                    {"id": 5, "data": {"type": 12, "value": creative.get("native_cta") or "Подробнее"}},
                ],
            }
        }
        if erid:
            native["native"].setdefault("ext", {})["erid"] = erid
        adv_info = {v: creative.get(k) for k, v in
                    [("adv_name", "name"), ("adv_inn", "inn"), ("adv_ogrn", "ogrn"), ("adv_address", "address")]
                    if creative.get(k)}
        if adv_info:
            native["native"].setdefault("ext", {})["advertiser_info"] = adv_info
        if creative["type"] == "push":
            native["native"].setdefault("ext", {})["ad_format"] = "push"
        return json.dumps(native, ensure_ascii=False)
    # video: VAST with quartile tracking
    if creative.get("vast_xml"):
        return creative["vast_xml"]
    duration = creative.get("video_duration") or 15
    vevent = f"{base}/api/rtb/vevent?bid={bid_id}"
    creative_id = creative.get("creative_id") or creative.get("id") or ""
    return f"""<VAST version="3.0"><Ad id="{creative_id}"><InLine>
<AdSystem>CentralDSP</AdSystem>{_build_vast_extensions(creative, erid)}<AdTitle>{creative['name']}</AdTitle>
<Impression><![CDATA[{imp_pixel}]]></Impression>{f'<Impression><![CDATA[{third_imp}]]></Impression>' if third_imp else ''}
<Creatives><Creative><Linear><Duration>00:00:{duration:02d}</Duration>
<TrackingEvents>
<Tracking event="start"><![CDATA[{vevent}&e=start]]></Tracking>
<Tracking event="firstQuartile"><![CDATA[{vevent}&e=firstQuartile]]></Tracking>
<Tracking event="midpoint"><![CDATA[{vevent}&e=midpoint]]></Tracking>
<Tracking event="thirdQuartile"><![CDATA[{vevent}&e=thirdQuartile]]></Tracking>
<Tracking event="complete"><![CDATA[{vevent}&e=complete]]></Tracking>
<Tracking event="skip"><![CDATA[{vevent}&e=skip]]></Tracking>
</TrackingEvents>
<VideoClicks><ClickThrough><![CDATA[{creative.get('click_url') or ''}]]></ClickThrough>
<ClickTracking><![CDATA[{click_url}]]></ClickTracking>{f'<ClickTracking><![CDATA[{third_click}]]></ClickTracking>' if third_click else ''}</VideoClicks>
<MediaFiles><MediaFile delivery="progressive" type="video/mp4" width="640" height="360"><![CDATA[{creative.get('video_url') or ''}]]></MediaFile></MediaFiles>
</Linear></Creative></Creatives></InLine></Ad></VAST>"""


def _fire_and_forget(coro) -> None:
    """Run bid-path bookkeeping (event log, stats) off the critical path.

    Bid responses must not wait on analytics writes: SSPs enforce tmax and
    drop slow bids. Win/imp/click handlers still await their writes because
    those are billing-relevant. Errors are logged, never propagated.
    """
    import asyncio

    task = asyncio.get_running_loop().create_task(coro)

    def _swallow(t: asyncio.Task) -> None:
        exc = t.exception()
        if exc is not None:
            logger.warning("background bookkeeping failed: %s", exc)

    task.add_done_callback(_swallow)


def _is_test_bid(row) -> bool:
    """bidmeta (Go пишет is_test=1 при OpenRTB test=1) или строка bid_events (034)."""
    try:
        v = row.get("is_test") if hasattr(row, "get") else row["is_test"]
    except (KeyError, IndexError, TypeError):
        return False
    return v in (1, "1", True)


def _row_get(row, key: str):
    """bidmeta (dict из Redis) или asyncpg.Record (PG-fallback) — единый доступ."""
    if row is None:
        return None
    if hasattr(row, "get"):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _cpc_of(row) -> float | None:
    """Цена клика CPC-бида из bidmeta (пишут оба биддера), иначе None = CPM.

    PG-fallback строка bid_events этих полей не несёт: клик по биду старше
    TTL bidmeta (1 ч) не биллится — недосписание, никогда не пересписание."""
    if (_row_get(row, "bid_type") or "cpm") != "cpc":
        return None
    try:
        cpc = float(_row_get(row, "bid_cpc") or 0)
    except (TypeError, ValueError):
        return None
    return cpc if cpc > 0 else None


def _event_dims(row) -> dict[str, Any]:
    """Измерения бида, наследуемые win/imp/click/view/loss (audit E-01).

    Раньше win/imp/click писались без bundle/publisher/tagid/region/city —
    в группировках «по площадкам» они попадали в «—», CTR/win-rate по
    конкретному inventory были недостоверны.
    """
    out: dict[str, Any] = {
        "imp_id": _row_get(row, "imp_id"),
        "campaign_id": _row_get(row, "campaign_id"),
        "line_item_id": _row_get(row, "line_item_id"),
        "creative_id": _row_get(row, "creative_id"),
        "supply_partner_id": _row_get(row, "supply_partner_id"),
    }
    for col in INVENTORY_DIMENSIONS:
        v = _row_get(row, col)
        if isinstance(v, str):
            v = v.strip() or None
            if v and col == "domain":
                v = v.lower()
        out[col] = v
    return out


async def _log_event(pool, event_type: str, **kw: Any) -> None:
    """Строка bid_events. Договор о расходе (stats_math): clear_price несёт
    только оплаченное событие — win (CPM) или click (CPC, audit R-03);
    impression/viewable/video его не дублируют."""
    await pool.execute(
        """
        INSERT INTO bid_events (event_type, bid_id, imp_id, campaign_id, line_item_id, creative_id,
                                supply_partner_id, price, clear_price, device_type, geo_country, geo_region, geo_city,
                                ad_type, domain, bundle, publisher_id, tagid, os, browser, pctr, is_test)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22)
        """,
        event_type, kw.get("bid_id", ""), kw.get("imp_id"), kw.get("campaign_id"),
        kw.get("line_item_id"), kw.get("creative_id"), kw.get("supply_partner_id"),
        kw.get("price"), kw.get("clear_price") if event_type in ("win", "click") else None,
        kw.get("device_type"), kw.get("geo_country"), kw.get("geo_region"), kw.get("geo_city"),
        kw.get("ad_type"), kw.get("domain"), kw.get("bundle"), kw.get("publisher_id"), kw.get("tagid"),
        kw.get("os"), kw.get("browser"), kw.get("pctr"),
        bool(kw.get("is_test", False)),
    )


async def _bump_stats(db, col: str, spend: float, campaign_id, line_item_id, creative_id, supply_partner_id) -> None:
    await db.execute(
        f"""
        INSERT INTO stats_hourly (ts_hour, campaign_id, line_item_id, creative_id, supply_partner_id, {col}, spend)
        VALUES (date_trunc('hour', now()), $1, $2, $3, $4, 1, $5)
        ON CONFLICT (ts_hour, campaign_id, line_item_id, creative_id, supply_partner_id)
        DO UPDATE SET {col} = stats_hourly.{col} + 1, spend = stats_hourly.spend + $5
        """,
        campaign_id, line_item_id, creative_id, supply_partner_id, spend,
    )


# --- Идемпотентность трекинг-событий (audit E-02) -----------------------------
#
# Источник истины — строка bid_event_once(bid_id, kind), вставляемая в ОДНОЙ
# транзакции с INSERT bid_events и UPSERT stats_hourly. Если любой шаг падает,
# откатывается и заявка — повтор пикселя дописывает всё целиком. Redis
# `once:*` — только fast-path для отсечения дублей, и ставится ПОСЛЕ коммита:
# ключ никогда не переживёт неудавшуюся запись.


async def _seen_fast(kind: str, bid: str) -> bool:
    try:
        return bool(await get_redis().exists(f"once:{kind}:{bid}"))
    except Exception:
        return False  # Redis недоступен — решает БД


async def _remember_fast(kind: str, bid: str) -> None:
    try:
        await mark_once(kind, bid)
    except Exception:
        logger.warning("once fast-path not cached for %s:%s", kind, bid)


async def _claim_once(conn, kind: str, bid: str) -> bool:
    return bool(await conn.fetchval(
        "INSERT INTO bid_event_once (bid_id, kind) VALUES ($1, $2) ON CONFLICT DO NOTHING RETURNING true",
        bid, kind,
    ))


@asynccontextmanager
async def _once_txn(pool, kind: str, bid: str):
    """Транзакция «ровно один раз» для (bid, kind).

    Отдаёт соединение с открытой транзакцией и уже вставленной заявкой, либо
    None, если событие уже учтено. Коммит — на выходе из блока; исключение в
    теле откатывает и заявку, и все записи внутри.
    """
    if await _seen_fast(kind, bid):
        yield None
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await _claim_once(conn, kind, bid):
                yield None
                return
            yield conn
    await _remember_fast(kind, bid)


def _stat_ids(row) -> tuple:
    return (row["campaign_id"], row["line_item_id"], row["creative_id"], row["supply_partner_id"])



def _parse_freq_caps(c) -> list[tuple[int, int]]:
    """Multi-window caps from campaigns.frequency_caps (JSONB), legacy pair as fallback."""
    raw = None
    try:
        raw = c["frequency_caps"]
    except (KeyError, IndexError):
        raw = None
    windows: list[tuple[int, int]] = []
    if raw:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = None
        if isinstance(raw, list):
            for w in raw[:3]:
                try:
                    imp = int(w.get("impressions", 0))
                    hrs = int(w.get("period_hours", 24))
                except (AttributeError, TypeError, ValueError):
                    continue
                if imp > 0 and hrs > 0:
                    windows.append((imp, hrs))
    if not windows:
        try:
            legacy = int(c["frequency_cap_impressions"] or 0)
        except (KeyError, IndexError, TypeError):
            legacy = 0
        if legacy > 0:
            try:
                hrs = int(c["frequency_cap_period_hours"] or 24)
            except (KeyError, IndexError, TypeError):
                hrs = 24
            windows.append((legacy, hrs))
    return windows


@router.post("/bid/{endpoint_key}")
async def bid(endpoint_key: str, request: Request):
    obs_metrics.inc("rtb_bid_requests_total")
    started = time.monotonic()
    if not _RTB_PYTHON_SERVING:
        # 204 no-bid: SSP-совместимый ответ, не ломает партнёрские интеграции
        obs_metrics.inc_no_bid("python_serving_off")
        return Response(status_code=204)
    try:
        req = await request.json()
    except Exception:
        obs_metrics.inc_no_bid("parse_error")
        raise HTTPException(status_code=400, detail="Invalid BidRequest JSON")
    # Throttling-предфильтр (Часть C): O(1)-отсев ботов/чужого гео/datacenter-IP
    # ДО полного цикла (Redis/Postgres). Отдельная метрика — это не no-bid.
    drop_reason = prefilter.check(req)
    if drop_reason is not None:
        obs_metrics.inc_prefilter(drop_reason)
        return Response(status_code=204)
    ssp_token = request.headers.get("x-centraldsp-token")
    client_ip = request.client.host if request.client else None
    try:
        result = await process_bid_request(endpoint_key, req, ssp_token=ssp_token, client_ip=client_ip)
    finally:
        obs_metrics.observe_bid_latency(time.monotonic() - started)
    if result is None:
        obs_metrics.inc_no_bid("no_candidates")
        return Response(status_code=204)
    bids = [b for sb in result.get("seatbid", []) for b in sb.get("bid", [])]
    obs_metrics.inc("rtb_bid_responses_total")
    obs_metrics.inc("rtb_bids_total", len(bids))
    obs_metrics.inc("rtb_bid_price_cpm_rub_sum", sum(float(b.get("price") or 0) for b in bids))
    return result


# ---------------------------------------------------------------------------
# In-process TTL cache for the bid hot path (always-on bidder only).
#
# Candidate line items, partner rows and optimizer blocks change on human
# timescales (seconds+), while the bidder answers 50-200 QPS. Caching them
# in-process for RTB_CACHE_TTL seconds collapses 3 sequential DB roundtrips
# per request into ~0. Budgets/QPS/freq-caps stay in Redis (cross-replica).
# RTB_CACHE_TTL=0 disables caching (tests, simulator, serverless deploys).
# ---------------------------------------------------------------------------
RTB_CACHE_TTL = float(os.environ.get("RTB_CACHE_TTL", "0"))
# Serve stale values for up to 5x TTL while ONE coroutine refreshes in the
# background (single-flight). Prevents the thundering herd at TTL expiry:
# without it, every in-flight request cache-misses simultaneously and fires
# 3 sequential DB queries each, spiking p99 by 10-20x.
RTB_CACHE_STALE_FACTOR = 5.0

_hot_cache: dict = {}
_refreshing: set = set()


def _cache_get(key: str):
    """Returns (value, fresh) - value may be stale; None means hard miss."""
    if RTB_CACHE_TTL <= 0:
        return None, False
    hit = _hot_cache.get(key)
    if hit is None:
        return None, False
    value, expires = hit
    now = time.monotonic()
    if now < expires:
        return value, True
    if now < expires + RTB_CACHE_TTL * (RTB_CACHE_STALE_FACTOR - 1):
        return value, False  # stale but usable
    _hot_cache.pop(key, None)
    return None, False


def _cache_put(key: str, value) -> None:
    if RTB_CACHE_TTL > 0:
        _hot_cache[key] = (value, time.monotonic() + RTB_CACHE_TTL)
    _refreshing.discard(key)


def _claim_refresh(key: str) -> bool:
    """True for exactly one caller per expiry window (single-flight)."""
    if key in _refreshing:
        return False
    _refreshing.add(key)
    return True


async def process_bid_request(
    endpoint_key: str,
    req: dict,
    ssp_token: str | None = None,
    client_ip: str | None = None,
    *,
    verify_token: bool = True,
):
    """Core bid logic. Returns a BidResponse dict or None (no-bid).
    Raises 404 for unknown endpoint keys, 403 for a bad SSP token or
    IP outside the partner whitelist, 429 when the partner exceeds QPS.
    Partners in 'testing' status are served: this is the SSP onboarding
    test phase (Часть SSP-онбординга).

    verify_token=False — только для внутренней автопроверки (ssp_check):
    открытый токен в БД не хранится (миграция 032), поэтому сервер сам
    предъявить его не может. Публичный HTTP-роут всегда проверяет."""
    pool = await get_pool()
    _pkey = f"partner:{endpoint_key}"
    partner, _fresh = _cache_get(_pkey)
    if partner is None or (not _fresh and _claim_refresh(_pkey)):
        partner = await pool.fetchrow(
            "SELECT * FROM supply_partners WHERE endpoint_key = $1 AND status IN ('active','testing')", endpoint_key
        )
        if partner is not None:
            _cache_put(_pkey, partner)
        else:
            _refreshing.discard(_pkey)
    if not partner:
        raise HTTPException(status_code=404, detail="Unknown endpoint")

    # Optional per-partner shared secret. В БД только sha256-хэш (миграция 032):
    # NULL/пусто = токен не выдан или отозван -> 403 всегда (fail-closed, audit
    # S-06; паритет с Go partner_auth.go). В окне ротации принимается и
    # предыдущий хэш (token_prev_hash до token_prev_valid_until, миграция 039).
    keys = partner.keys()
    expected_hash = partner["auth_token_hash"] if "auth_token_hash" in keys else None
    if not expected_hash:
        raise HTTPException(status_code=403, detail="SSP token not issued")
    if verify_token and not ssp_tokens.verify_token(
        ssp_token, expected_hash,
        partner["token_prev_hash"] if "token_prev_hash" in keys else None,
        partner["token_prev_valid_until"] if "token_prev_valid_until" in keys else None,
    ):
        raise HTTPException(status_code=403, detail="Invalid SSP token")

    # IP whitelist партнёра (миграция 030): непустой список = только эти IP/CIDR.
    # client_ip=None (симулятор/автопроверка) — внутренний вызов, проверка не нужна.
    if client_ip is not None and "ip_whitelist" in partner.keys():
        _wl = partner["ip_whitelist"]
        if isinstance(_wl, str):
            _wl = json.loads(_wl)
        if _wl and not ssp_check.ip_allowed(client_ip, _wl):
            raise HTTPException(status_code=403, detail="IP not in partner whitelist")

    # Per-partner QPS limit, enforced across all replicas via Redis.
    if not await check_qps(str(partner["id"]), partner["qps_limit"] or 0):
        raise HTTPException(status_code=429, detail="QPS limit exceeded")

    req_id = req.get("id") or str(uuid.uuid4())
    imps = req.get("imp") or []
    if not imps:
        return None
    try:
        # OpenRTB 2.5: 1 = first price, 2 = second price (default when absent)
        auction_type = int(req.get("at") or 2)
    except (TypeError, ValueError):
        auction_type = 2

    device = req.get("device") or {}
    geo = (device.get("geo") or {})
    site = req.get("site") or {}
    device_id = device.get("ifa") or device.get("ip") or ""
    allowed_types = partner["allowed_ad_types"]
    if isinstance(allowed_types, str):
        allowed_types = json.loads(allowed_types)

    # Candidate line items: active campaign within budget & dates, active LI, approved creative
    candidates, _fresh = _cache_get("candidates")
    if candidates is None or (not _fresh and _claim_refresh("candidates")):
        # erid-гейт — паритет с /rtb-feed/campaigns (ord.service.ERID_FEED_GATE_SQL)
        candidates = await pool.fetch(
        f"""
        SELECT li.id AS li_id, li.bid_price, li.bid_type, li.max_bid, li.daily_budget AS li_budget,
               li.targeting, li.campaign_id, li.user_id AS owner_id,
               c.daily_budget AS camp_budget, c.frequency_cap_impressions, c.frequency_cap_period_hours, c.frequency_caps,
               c.pacing, c.advertiser_id, c.bid_shading, c.objective, c.clean_traffic_only,
               adv.domain AS adv_domain,
               adv.balance AS adv_balance, c.total_budget, c.spent_total,
               cr.id AS creative_id, cr.type, cr.width, cr.height, cr.asset_url, cr.click_url,
               cr.html_snippet, cr.native_title, cr.native_description, cr.native_cta,
               cr.native_icon_url, cr.video_url, cr.video_duration, cr.vast_xml, cr.name,
               cr.imp_tracker_url, cr.click_tracker_url, cr.erid,
               cp.name AS adv_name, cp.inn AS adv_inn, cp.ogrn AS adv_ogrn, cp.address AS adv_address
        FROM line_items li
        JOIN campaigns c ON c.id = li.campaign_id AND c.status = 'active'
             AND (c.start_date IS NULL OR c.start_date <= CURRENT_DATE)
             AND (c.end_date IS NULL OR c.end_date >= CURRENT_DATE)
             AND (c.total_budget <= 0 OR c.spent_total < c.total_budget)
        JOIN advertisers adv ON adv.id = c.advertiser_id AND adv.status = 'active' AND adv.balance > 0
        JOIN creatives cr ON cr.campaign_id = c.id AND cr.status = 'approved'
             AND (cr.line_item_id IS NULL OR cr.line_item_id = li.id)
        LEFT JOIN counterparties cp ON cp.id = cr.counterparty_id
        WHERE li.status = 'active' AND li.bid_price > 0
        {feed_gate_sql()}
        """
        )
        _cache_put("candidates", candidates)
    if not candidates:
        return None

    # Optimizer block lists — any dimension the platform observes can be blocked
    camp_ids = list({c["campaign_id"] for c in candidates})
    blocks_key = "blocks:" + ",".join(sorted(str(i) for i in camp_ids))
    block_rows, _fresh = _cache_get(blocks_key)
    if block_rows is None or (not _fresh and _claim_refresh(blocks_key)):
        block_rows = await pool.fetch(
            "SELECT campaign_id, block_type, block_value FROM optimizer_blocks WHERE campaign_id = ANY($1)",
            camp_ids,
        )
        _cache_put(blocks_key, block_rows)
    blocks: dict = {}
    for b in block_rows:
        blocks.setdefault(str(b["campaign_id"]), set()).add((b["block_type"], b["block_value"].lower()))

    # Глобальные white/blacklist площадок (уровень аккаунта, раздел Настройки).
    # Кэшируются так же, как блоки оптимизатора: TTL короткий, меняются людьми.
    owner_ids = list({c["owner_id"] for c in candidates})
    dl_key = "domain_lists:" + ",".join(sorted(str(i) for i in owner_ids))
    dl_rows, _fresh = _cache_get(dl_key)
    if dl_rows is None or (not _fresh and _claim_refresh(dl_key)):
        dl_rows = await pool.fetch(
            "SELECT user_id, domain, list_type FROM domain_lists WHERE user_id = ANY($1)",
            owner_ids,
        )
        _cache_put(dl_key, dl_rows)
    g_black: dict = {}
    g_white: dict = {}
    for r in dl_rows:
        target = g_black if r["list_type"] == "black" else g_white
        target.setdefault(str(r["user_id"]), set()).add(r["domain"].lower())

    from datetime import datetime, timezone as _tz

    from ml import pctr as ml_pctr
    from ml import quality as ml_quality

    req_domain = ((req.get("site") or req.get("app") or {}).get("domain")
                  or (req.get("app") or {}).get("bundle") or "").lower()
    # Clean-traffic (Часть B): set фродовых доменов из in-process кэша.
    # Fail-open: нет данных -> пустой set, закупка не останавливается.
    _dirty_domains: frozenset = frozenset()
    if req_domain and any(c["clean_traffic_only"] for c in candidates):
        _dirty_domains = await ml_quality.get_blocked_domains(pool)
    # Scoring-профили (Часть 3b): per-campaign блок-лист доменов, рассчитанный
    # пользовательским профилем в enforce-режиме. Fail-open через кэш.
    _scoring_blocks: dict[str, frozenset] = {}
    if req_domain:
        import scoring as _scoring
        for _cid in {str(c["campaign_id"]) for c in candidates}:
            blocked = await _scoring.get_scoring_blocks(pool, _cid)
            if blocked:
                _scoring_blocks[_cid] = blocked
    req_device = DEVICE_TYPES.get((req.get("device") or {}).get("devicetype", 0), "unknown")
    req_geo = (geo.get("country") or "").lower()
    req_os = _device_os(device)
    req_browser = _device_browser(device)
    req_hour = str(datetime.now(_tz.utc).hour)

    seatbids = []
    base = _base_url()

    for imp in imps:
        ad_type = _imp_ad_type(imp)
        if not ad_type or ad_type not in allowed_types:
            continue
        floor = max(float(imp.get("bidfloor") or 0), float(partner["bid_floor"]))

        best = None
        for cand in candidates:
            c = dict(cand)
            targeting = c["targeting"]
            if isinstance(targeting, str):
                targeting = json.loads(targeting)
            if not _creative_fits_imp(c, imp, ad_type):
                continue
            ssp_allow = targeting.get("ssp_allowlist") or []
            if ssp_allow and str(partner["id"]) not in [str(s) for s in ssp_allow]:
                continue
            # Optimizer block lists — every observable dimension is enforceable
            camp_blocks = blocks.get(str(c["campaign_id"]))
            if camp_blocks:
                if req_domain and ("domain", req_domain) in camp_blocks:
                    continue
                if ("supply_partner", str(partner["id"]).lower()) in camp_blocks:
                    continue
                if ("device_type", req_device) in camp_blocks:
                    continue
                if req_geo and ("geo_country", req_geo) in camp_blocks:
                    continue
                if ("os", req_os) in camp_blocks:
                    continue
                if ("browser", req_browser) in camp_blocks:
                    continue
                if ("ad_type", ad_type) in camp_blocks:
                    continue
                if ("creative", str(c["creative_id"]).lower()) in camp_blocks:
                    continue
                if ("line_item", str(c["li_id"]).lower()) in camp_blocks:
                    continue
                if ("hour", req_hour) in camp_blocks:
                    continue
            # Глобальные листы аккаунта: блэклист всегда исключает домен;
            # непустой вайтлист пропускает ТОЛЬКО перечисленные домены.
            owner = str(c["owner_id"])
            if req_domain:
                if owner in g_black and req_domain in g_black[owner]:
                    continue
                owner_white = g_white.get(owner)
                if owner_white and req_domain not in owner_white:
                    continue
            # Clean-traffic (Часть B): кампания с тумблером не биддит на домены
            # с низким quality score. Только opt-in, только известные домены.
            if c["clean_traffic_only"] and req_domain and req_domain in _dirty_domains:
                continue
            # Scoring-профиль кампании (enforce): домен ниже порога качества — не биддим.
            if req_domain:
                _sb = _scoring_blocks.get(str(c["campaign_id"]))
                if _sb and req_domain in _sb:
                    continue
            if not _match_targeting(targeting, req):
                continue
            if not _match_imp_targeting(targeting, imp):
                continue
            # Retargeting: audience include/exclude via Redis sets (fail-open)
            _aud_inc = targeting.get("audience_include") or []
            _aud_exc = targeting.get("audience_exclude") or []
            if (_aud_inc or _aud_exc) and not await check_audiences(
                    device_id or "", [str(x) for x in _aud_inc], [str(x) for x in _aud_exc]):
                continue
            # CPC (audit R-03): ставка за клик -> эквивалентная CPM через
            # ожидаемый CTR (pCTR-модель, иначе консервативный fallback).
            # Списание — по клику ровно bid_cpc (см. click()); win не списывает.
            # max_bid для CPC — потолок клика, а не CPM.
            c["_bid_type"] = (c.get("bid_type") or "cpm")
            c["_bid_cpc"] = None
            if c["_bid_type"] == "cpc":
                bid_cpc = float(c["bid_price"])
                if c["max_bid"]:
                    bid_cpc = min(bid_cpc, float(c["max_bid"]))
                ctr = await ml_pctr.expected_ctr(
                    pool, domain=req_domain or None, creative_id=str(c["creative_id"]),
                    device_type=req_device, hour=int(req_hour),
                )
                price = ml_pctr.cpc_to_cpm(bid_cpc, ctr)
                c["_bid_cpc"] = bid_cpc
            else:
                price = float(c["bid_price"])
                if c["max_bid"]:
                    price = min(price, float(c["max_bid"]))
            if price < floor:
                continue
            # Value-based биддинг (Часть B): для performance-целей корректируем
            # бид множителем pctr/baseline с жёсткими guardrails:
            #   * только objective clicks/conversions и bid_type CPM;
            #   * множитель зажат в [0.7, 1.3] — модель тюнингует, не управляет;
            #   * пол floor и потолок max_bid соблюдаются всегда;
            #   * модель недоступна -> базовая цена (fail-safe).
            c["_pctr"] = None
            if (c.get("objective") in ("traffic", "conversions")
                    and (c.get("bid_type") or "cpm") == "cpm"):
                _pred = await ml_pctr.predict(
                    pool, domain=req_domain or None,
                    creative_id=str(c["creative_id"]),
                    device_type=req_device, hour=int(req_hour),
                )
                if _pred is not None and _pred["matched"] > 0 and _pred["baseline"] > 0:
                    _mult = min(1.3, max(0.7, _pred["pctr"] / _pred["baseline"]))
                    price = max(floor, price * _mult)
                    if c["max_bid"]:
                        price = min(price, float(c["max_bid"]))
                    c["_pctr"] = _pred["pctr"]
            # Bid shading (opt-in, first-price only): pay closer to the
            # clearing price, never below floor. Second-price auctions are
            # excluded: shading there cuts win rate without saving money.
            if should_shade(c["bid_shading"], auction_type):
                _sf = await shading_get_factor(str(c["campaign_id"]), str(partner["id"]))
                if _sf < 1.0:
                    price = max(floor * 1.01, price * _sf)
                    if c["max_bid"] and c["_bid_type"] != "cpc":
                        price = min(price, float(c["max_bid"]))
            if best is None or price > best[0]:
                c["_deal_id"] = _matched_deal(targeting, imp)
                best = (price, c)

        if not best:
            continue

        price, c = best
        cost = price / 1000.0  # CPM -> cost per impression

        # Even pacing: cap the effective daily budget by the share of the
        # BUSINESS day elapsed (Europe/Moscow — the same boundary as spend:*).
        # Shared helper; Go mirrors it in redisx.EvenPacingBudget (audit R-03).
        effective_budget = float(c["camp_budget"])
        if (c.get("pacing") or "asap") == "even" and effective_budget > 0:
            effective_budget = even_pacing_budget(effective_budget)

        # Multi-window frequency caps (up to 3); legacy pair is the fallback
        caps_list = _parse_freq_caps(c)
        bid_id = str(uuid.uuid4())
        # Single Redis roundtrip (audit M-05/M-06): frequency-cap check +
        # atomic reservation on campaign daily, line-item daily, advertiser
        # available balance and total-budget remaining; bid tracked in
        # pending:bids until win/loss/expire.
        total_budget = float(c.get("total_budget") or 0)
        # OpenRTB test=1 (§3.2.1, audit E-04): SSP проверяет интеграцию. Отвечаем
        # реальным бидом, но бюджет не резервируем, bids не считаем, а is_test
        # едет в bidmeta и bid_events — win/imp/click по нему не тронут ни деньги,
        # ни stats_hourly. Зеркало Go handler.go (req.Test == 1).
        is_test_req = req.get("test") in (1, "1", True)
        reserve_meta = {
            "advertiser_id": str(c.get("advertiser_id") or ""),
            "reserve": 0 if is_test_req else cost,
            "has_total": 1 if total_budget > 0 else 0,
            "has_li": 1 if float(c["li_budget"] or 0) > 0 else 0,
            "business_date": business_date_str(),
        }
        if not is_test_req:
            ok = await gate_and_reserve(
                str(c["campaign_id"]), str(c["li_id"]), cost,
                effective_budget, float(c["li_budget"] or 0),
                device_id=device_id or "",
                caps=caps_list,
                bid_id=bid_id,
                advertiser_id=reserve_meta["advertiser_id"],
                adv_balance=float(c.get("adv_balance") or 0),
                total_remaining=total_budget - float(c.get("spent_total") or 0),
                has_total=total_budget > 0,
            )
            if not ok:
                continue
        event_token = sign_event_token(bid_id)
        # Retargeting bridge: pixel reports clid == bid_id after the click
        if device_id:
            _fire_and_forget(store_clid_bridge(bid_id, device_id))
        nurl = f"{base}/api/rtb/win?bid={event_token}&price=${{AUCTION_PRICE}}"
        adm = _build_adm(c, base, event_token)

        # Remember device for frequency counting at impression time
        if device_id and caps_list:
            r = get_redis()
            _fire_and_forget(r.set(
                f"bidfreq:{bid_id}",
                f"{device_id}|{json.dumps(caps_list)}",
                ex=3600,
            ))

        # Synchronously cache bid metadata in Redis (sub-ms) so win/imp/click
        # handlers can resolve the bid immediately; the Postgres insert below
        # is fire-and-forget and may land after the SSP's win notice (GAP-011).
        # Одни и те же inventory-измерения идут в bidmeta и в событие bid —
        # win/imp/click наследуют их через _event_dims (audit E-01). Формат
        # полей — контракт с Go persistBidMeta.
        _site_or_app = req.get("site") or req.get("app") or {}
        bid_dims = {
            "device_type": DEVICE_TYPES.get(device.get("devicetype", 0), "unknown"),
            "geo_country": geo.get("country"), "geo_region": geo.get("region"), "geo_city": geo.get("city"),
            "ad_type": ad_type,
            "domain": site.get("domain"), "bundle": (req.get("app") or {}).get("bundle"),
            "publisher_id": str((_site_or_app.get("publisher") or {}).get("id") or "") or None,
            "tagid": imp.get("tagid"),
            "os": req_os, "browser": req_browser,
        }
        await get_redis().set(
            f"bidmeta:{bid_id}",
            json.dumps({
                "bid_id": bid_id, "imp_id": imp.get("id"),
                "campaign_id": str(c["campaign_id"]), "line_item_id": str(c["li_id"]),
                "creative_id": str(c["creative_id"]), "supply_partner_id": str(partner["id"]),
                "price": price,
                **bid_dims,
                "is_test": 1 if is_test_req else 0,
                # CPC (R-03): win() не списывает, click() списывает bid_cpc.
                "bid_type": c["_bid_type"],
                "bid_cpc": c["_bid_cpc"],
                **reserve_meta,
            }),
            ex=3600,
        )
        _fire_and_forget(_log_event(
            pool, "bid", bid_id=bid_id, imp_id=imp.get("id"),
            campaign_id=c["campaign_id"], line_item_id=c["li_id"], creative_id=c["creative_id"],
            supply_partner_id=partner["id"], price=price, pctr=c.get("_pctr"),
            is_test=is_test_req, **bid_dims,
        ))
        if not is_test_req:
            _fire_and_forget(_bump_stats(pool, "bids", 0, c["campaign_id"], c["li_id"], c["creative_id"], partner["id"]))

        bid_obj = {
            "id": bid_id,
            "impid": imp.get("id"),
            "price": round(price, 4),
            "nurl": nurl,
            "adm": adm,
            "crid": str(c["creative_id"]),
            "cid": str(c["campaign_id"]),
            "adomain": [c["adv_domain"]] if c.get("adv_domain") else [],
            "w": c.get("width"),
            "h": c.get("height"),
        }
        if c.get("_deal_id"):
            bid_obj["dealid"] = c["_deal_id"]
        # seat: ID, который этот SSP присвоил нам как байеру (миграция 030)
        _seat = (partner["seat_id"] if "seat_id" in partner.keys() else None) or "centraldsp"
        seatbids.append({"bid": [bid_obj], "seat": _seat})

    if not seatbids:
        return None

    # cur: валюта из онбординг-анкеты партнёра (миграция 030), иначе платформенная
    _cur = (partner["currency"] if "currency" in partner.keys() else None) or CURRENCY
    return {
        "id": req_id,
        "bidid": str(uuid.uuid4()),
        "cur": _cur,
        "seatbid": seatbids,
    }


async def _bump_domain_stats(db, row, col: str, spend=0):
    """Upsert domain_stats_hourly from a found bid_events row. Best-effort.

    `db` — пул или соединение с открытой транзакцией. Во втором случае запрос
    идёт под savepoint: проглоченная ошибка не должна переводить внешнюю
    транзакцию (событие + stats_hourly) в состояние aborted.
    """
    domain = (row.get("domain") or "").strip().lower() if hasattr(row, "get") else (row["domain"] or "").strip().lower()
    if not domain:
        return
    sql = f"""
        INSERT INTO domain_stats_hourly (ts_hour, campaign_id, supply_partner_id, domain, {col}, spend)
        VALUES (date_trunc('hour', now()), $1, $2, $3, 1, $4)
        ON CONFLICT (ts_hour, campaign_id, supply_partner_id, domain)
        DO UPDATE SET {col} = domain_stats_hourly.{col} + 1,
                      spend = domain_stats_hourly.spend + $4
    """
    args = (row["campaign_id"], row["supply_partner_id"], domain, spend or 0)
    try:
        if hasattr(db, "transaction"):
            async with db.transaction():
                await db.execute(sql, *args)
        else:
            await db.execute(sql, *args)
    except Exception:
        logger.exception("domain stats bump failed (non-fatal)")


async def _find_bid(pool, bid_id: str):
    """Resolve bid metadata: Redis cache first (written synchronously in the
    bid path), Postgres bid_events as fallback for older bids."""
    try:
        raw = await get_redis().get(f"bidmeta:{bid_id}")
        if raw:
            return json.loads(raw)
    except Exception:
        logger.exception("bidmeta redis lookup failed; falling back to PG")
    return await pool.fetchrow(
        "SELECT * FROM bid_events WHERE bid_id = $1 AND event_type = 'bid' ORDER BY id LIMIT 1", bid_id
    )


@router.get("/win")
async def win_notice(bid: str, price: str | None = None):
    """Win notice (nurl). `price` is the SSP-substituted ${AUCTION_PRICE};
    it is untrusted input: clamped to [0, our bid price] before billing.
    Billing is transactional and idempotent (see billing_core.bill_win).
    `bid` is an HMAC-signed event token (see tokens.py)."""
    bid = verify_event_token(bid)
    if not bid:
        obs_metrics.inc("rtb_event_token_rejects_total")
        raise HTTPException(status_code=403, detail="Invalid event token")
    pool = await get_pool()
    row = await _find_bid(pool, bid)
    if not row:
        raise HTTPException(status_code=404, detail="Unknown bid")
    clear_price = clamp_clearing_price(price, row["price"] or 0)
    cost = cpm_to_cost(clear_price)

    # OpenRTB test=1 (bidmeta.is_test от Go-биддера): SSP проверяет интеграцию.
    # Фиксируем win в журнале (видно в отчёте с флагом), но не списываем деньги
    # и не трогаем stats_hourly — иначе тест SSP исказит расход клиента.
    if _is_test_bid(row):
        async with _once_txn(pool, "testwin", bid) as conn:
            if conn is not None:
                obs_metrics.inc("rtb_test_wins_total")
                await _log_event(
                    conn, "win", bid_id=bid, price=row["price"], clear_price=clear_price,
                    is_test=True, **_event_dims(row),
                )
        return {"ok": True, "test": True}

    # CPC (audit R-03): win — событие доставки, не оплаты. Деньги списывает
    # click() ровно на bid_cpc; здесь только журнал (без clear_price — договор
    # stats_math) и счётчик wins. Резерв Redis (ожидаемая стоимость показа)
    # переводится в «потрачено» как есть: дневные лимиты и pacing работают по
    # ожиданию, источник истины по деньгам — ledger.
    if _cpc_of(row) is not None:
        async with _once_txn(pool, "win", bid) as conn:
            if conn is not None:
                await _log_event(conn, "win", bid_id=bid, price=row["price"], **_event_dims(row))
                await _bump_stats(conn, "wins", 0, *_stat_ids(row))
                await _bump_domain_stats(conn, row, "wins")
                obs_metrics.inc("rtb_wins_total")
                obs_metrics.inc("rtb_cpc_wins_total")
        try:
            res = reservation_from_bidmeta(row)
            if res:
                res["bid_id"] = bid
                await settle_win(res, float(res.get("reserve") or 0), assume_released=not isinstance(row, dict))
        except Exception:
            logger.exception("settle_win (cpc) failed for bid %s", bid)
            obs_metrics.inc("rtb_settle_win_errors_total")
        return {"ok": True, "cpc": True}

    # Audit E-02: списание, событие win и stats_hourly — одна транзакция.
    # bill_win внутри открывает свою transaction() -> под внешней это savepoint.
    # Дедуп — transactions(bid_id) WHERE type='spend': повтор после падения
    # любого шага снова проходит весь путь целиком, а не «duplicate».
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await bill_win(
                conn,
                bid_id=bid,
                campaign_id=row["campaign_id"],
                line_item_id=row["line_item_id"],
                cost=cost,
            )
            if not result.billed:
                return {"ok": True, "duplicate": True}
            await _log_event(
                conn, "win", bid_id=bid, price=row["price"], clear_price=clear_price,
                **_event_dims(row),
            )
            await _bump_stats(conn, "wins", cost, *_stat_ids(row))
            await _bump_domain_stats(conn, row, "wins", result.cost)

    # Audit M-06: перевести резерв Redis в фактическое списание. Резерв был
    # bid price/1000, списан clearing — разница возвращается на дневные ключи,
    # доступный баланс и остаток total. Если резерв уже вернулся по loss/expire
    # (или бид пришёл из PG-fallback без bidmeta), clearing досписывается.
    try:
        res = reservation_from_bidmeta(row)
        if res:
            res["bid_id"] = bid
            if not res.get("advertiser_id") and result.advertiser_id:
                # PG-fallback row без advertiser_id: bill_win его знает.
                res["advertiser_id"] = result.advertiser_id
            from_pg = not isinstance(row, dict)
            code = await settle_win(res, float(cost), assume_released=from_pg)
            obs_metrics.inc({0: "rtb_win_settle_duplicate_total", 2: "rtb_win_settle_recharged_total"}.get(code, "rtb_win_settle_converted_total"))
    except Exception:
        # Деньги в БД уже списаны (источник истины); Redis подтянет reconcile.
        logger.exception("settle_win failed for bid %s", bid)
        obs_metrics.inc("rtb_settle_win_errors_total")

    obs_metrics.inc("rtb_wins_total")
    obs_metrics.inc("rtb_win_spend_rub_sum", float(cost))
    return {"ok": True}


@router.get("/imp")
async def impression(bid: str):
    bid = verify_event_token(bid)
    if not bid:
        return Response(content=_PIXEL, media_type="image/gif")
    pool = await get_pool()
    # Бид ищем до заявки на дедуп: неизвестный bid не должен «сжигать» ключ
    # (bidmeta мог ещё не долететь в Redis, а PG-fallback — быть за партицией).
    row = await _find_bid(pool, bid)
    if not row:
        return Response(content=_PIXEL, media_type="image/gif")
    is_test = _is_test_bid(row)
    counted = False
    async with _once_txn(pool, "imp", bid) as conn:
        if conn is not None:
            await _log_event(conn, "impression", bid_id=bid, is_test=is_test, **_event_dims(row))
            if not is_test:
                # Тестовый показ SSP: в журнале есть, в статистике клиента — нет.
                await _bump_stats(conn, "impressions", 0, *_stat_ids(row))
                await _bump_domain_stats(conn, row, "impressions")
            counted = True
    if counted:
        obs_metrics.inc("rtb_imps_total")
    if counted and not is_test:
        # Count the impression against the frequency cap (после коммита: Redis
        # не откатить, а недосчитанный кап безопаснее пересчитанного).
        try:
            meta = await get_redis().get(f"bidfreq:{bid}")
        except Exception:
            meta = None
        if meta:
            device_id, _, payload = meta.partition("|")
            try:
                windows = [(int(a), int(b)) for a, b in json.loads(payload)]
            except Exception:
                # Legacy meta format: "device|period_hours"
                windows = [(0, int(payload or 24))]
            await bump_frequency_multi(str(row["campaign_id"]), device_id, windows)
    return Response(content=_PIXEL, media_type="image/gif")


_PIXEL = bytes.fromhex("47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b")


@router.get("/view")
async def viewable_impression(bid: str):
    """Viewability-маячок (MRC: >=50% площади в зоне видимости >=1 сек).

    Дёргается скриптом-замером из разметки баннера (IntersectionObserver),
    eventtrackers-пикселем натива/пуша или событием start видео. Идемпотентен
    через _once_txn — повторные срабатывания не задваивают статистику.
    """
    bid = verify_event_token(bid)
    if not bid:
        return Response(content=_PIXEL, media_type="image/gif")
    pool = await get_pool()
    row = await _find_bid(pool, bid)
    if row:
        await _record_viewable(pool, bid, row)
    return Response(content=_PIXEL, media_type="image/gif")


async def _record_viewable(pool, bid: str, row) -> None:
    """Событие viewable + viewable_imps ровно один раз на бид (общее для
    /rtb/view и video start). Тестовый бид (E-04): журнал — да, статистика — нет."""
    is_test = _is_test_bid(row)
    async with _once_txn(pool, "view", bid) as conn:
        if conn is None:
            return
        await _log_event(conn, "viewable", bid_id=bid, is_test=is_test, **_event_dims(row))
        if not is_test:
            await _bump_stats(conn, "viewable_imps", 0, *_stat_ids(row))
            await _bump_domain_stats(conn, row, "viewable_imps")
    if not is_test:
        obs_metrics.inc("rtb_viewable_imps_total")


_VIDEO_EVENTS = {"start", "firstQuartile", "midpoint", "thirdQuartile", "complete", "skip"}


@router.get("/vevent")
async def video_event(bid: str, e: str):
    """VAST tracking-event callback: start / quartiles / complete / skip."""
    if e not in _VIDEO_EVENTS:
        return Response(content=_PIXEL, media_type="image/gif")
    bid = verify_event_token(bid)
    if not bid:
        return Response(content=_PIXEL, media_type="image/gif")
    pool = await get_pool()
    row = await _find_bid(pool, bid)
    if not row:
        return Response(content=_PIXEL, media_type="image/gif")
    is_test = _is_test_bid(row)
    async with _once_txn(pool, f"vev:{e}", bid) as conn:
        if conn is not None:
            await _log_event(conn, f"video_{e}", bid_id=bid, is_test=is_test, **_event_dims(row))
            if is_test:
                pass   # тестовый бид (E-04): в журнале есть, в статистике нет
            elif e == "start":
                await _bump_stats(conn, "video_starts", 0, *_stat_ids(row))
            elif e == "complete":
                await _bump_stats(conn, "video_completes", 0, *_stat_ids(row))
    if e == "start":
        # Видео-viewability: старт плеера = объявление в зоне видимости.
        # Отдельная заявка "view" — не задвоится с /rtb/view.
        await _record_viewable(pool, bid, row)
    return Response(content=_PIXEL, media_type="image/gif")


@router.get("/click")
async def click(bid: str, r: str = ""):
    bid = verify_event_token(bid)
    if not bid:
        raise HTTPException(status_code=403, detail="Invalid event token")
    pool = await get_pool()
    row = await _find_bid(pool, bid)
    if row:
        is_test = _is_test_bid(row)
        cpc = None if is_test else _cpc_of(row)
        async with _once_txn(pool, "click", bid) as conn:
            if conn is not None:
                # CPC (audit R-03): оплаченное событие — первый клик по биду.
                # bill_win идемпотентен по bid_id (ledger type='spend'), поэтому
                # один показ оплачивается не более одного раза, сколько бы
                # кликов ни пришло; clear_price клика = цена клика (stats_math).
                charged = None
                if cpc is not None:
                    result = await bill_win(
                        conn, bid_id=bid, campaign_id=row["campaign_id"],
                        line_item_id=row["line_item_id"], cost=Decimal(str(cpc)),
                        description=f"Click bid={bid}",
                    )
                    if result.billed:
                        charged = result.cost
                        obs_metrics.inc("rtb_cpc_clicks_billed_total")
                        obs_metrics.inc("rtb_win_spend_rub_sum", float(charged))
                await _log_event(
                    conn, "click", bid_id=bid, is_test=is_test,
                    clear_price=charged, **_event_dims(row),
                )
                if not is_test:
                    # Тестовый клик SSP (E-04): в журнале есть, в статистике клиента — нет.
                    await _bump_stats(conn, "clicks", float(charged or 0), *_stat_ids(row))
                    await _bump_domain_stats(conn, row, "clicks", float(charged or 0))
                    obs_metrics.inc("rtb_clicks_total")
    # Redirect target: explicit r param, else fall back to the creative's click_url
    if not (r and r.startswith(("http://", "https://"))):
        if row and row["creative_id"]:
            r = await pool.fetchval("SELECT click_url FROM creatives WHERE id = $1", row["creative_id"]) or ""
    if r and r.startswith(("http://", "https://")):
        # Pass our click id to the landing page so the Central Pixel can attribute conversions
        sep = "&" if "?" in r else "?"
        return Response(status_code=302, headers={"Location": f"{r}{sep}cdsp_clid={bid}"})
    return {"ok": True}
