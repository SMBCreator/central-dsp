"""OpenRTB 2.5 bid endpoint + win/impression/click event tracking.

Flow:
  POST /rtb/bid/{endpoint_key}  <- BidRequest from SSP, returns BidResponse or 204
  GET  /rtb/win                 <- win notice (nurl), records win + spend
  GET  /rtb/imp                 <- impression tracker (burl)
  GET  /rtb/click               <- click tracker
"""
import json
import os
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from cache import bump_frequency, check_frequency_cap, get_redis, mark_once, reserve_budget
from db import get_pool

router = APIRouter(prefix="/rtb", tags=["rtb"])

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
    if creative["type"] != ad_type:
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


def _build_adm(creative: dict, base: str, bid_id: str) -> str:
    click_url = f"{base}/api/rtb/click?bid={bid_id}&r={creative.get('click_url') or ''}"
    imp_pixel = f"{base}/api/rtb/imp?bid={bid_id}"
    if creative["type"] == "banner":
        if creative.get("html_snippet"):
            return creative["html_snippet"] + f'<img src="{imp_pixel}" width="1" height="1" style="display:none"/>'
        return (
            f'<a href="{click_url}" target="_blank">'
            f'<img src="{creative.get("asset_url") or ""}" width="{creative.get("width")}" height="{creative.get("height")}" alt=""/></a>'
            f'<img src="{imp_pixel}" width="1" height="1" style="display:none"/>'
        )
    if creative["type"] == "native":
        native = {
            "native": {
                "link": {"url": click_url},
                "imptrackers": [imp_pixel],
                "assets": [
                    {"id": 1, "title": {"text": creative.get("native_title") or ""}},
                    {"id": 2, "data": {"type": 2, "value": creative.get("native_description") or ""}},
                    {"id": 3, "img": {"type": 3, "url": creative.get("asset_url") or ""}},
                    {"id": 4, "img": {"type": 1, "url": creative.get("native_icon_url") or ""}},
                    {"id": 5, "data": {"type": 12, "value": creative.get("native_cta") or "Подробнее"}},
                ],
            }
        }
        return json.dumps(native, ensure_ascii=False)
    # video: VAST
    if creative.get("vast_xml"):
        return creative["vast_xml"]
    duration = creative.get("video_duration") or 15
    return f"""<VAST version="3.0"><Ad id="{creative['id']}"><InLine>
<AdSystem>CentralDSP</AdSystem><AdTitle>{creative['name']}</AdTitle>
<Impression><![CDATA[{imp_pixel}]]></Impression>
<Creatives><Creative><Linear><Duration>00:00:{duration:02d}</Duration>
<VideoClicks><ClickThrough><![CDATA[{creative.get('click_url') or ''}]]></ClickThrough>
<ClickTracking><![CDATA[{click_url}]]></ClickTracking></VideoClicks>
<MediaFiles><MediaFile delivery="progressive" type="video/mp4" width="640" height="360"><![CDATA[{creative.get('video_url') or ''}]]></MediaFile></MediaFiles>
</Linear></Creative></Creatives></InLine></Ad></VAST>"""


async def _log_event(pool, event_type: str, **kw: Any) -> None:
    await pool.execute(
        """
        INSERT INTO bid_events (event_type, bid_id, imp_id, campaign_id, line_item_id, creative_id,
                                supply_partner_id, price, clear_price, device_type, geo_country, ad_type, domain, os, browser)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        """,
        event_type, kw.get("bid_id", ""), kw.get("imp_id"), kw.get("campaign_id"),
        kw.get("line_item_id"), kw.get("creative_id"), kw.get("supply_partner_id"),
        kw.get("price"), kw.get("clear_price"), kw.get("device_type"),
        kw.get("geo_country"), kw.get("ad_type"), kw.get("domain"),
        kw.get("os"), kw.get("browser"),
    )


async def _bump_stats(pool, col: str, spend: float, campaign_id, line_item_id, creative_id, supply_partner_id) -> None:
    await pool.execute(
        f"""
        INSERT INTO stats_hourly (ts_hour, campaign_id, line_item_id, creative_id, supply_partner_id, {col}, spend)
        VALUES (date_trunc('hour', now()), $1, $2, $3, $4, 1, $5)
        ON CONFLICT (ts_hour, campaign_id, line_item_id, creative_id, supply_partner_id)
        DO UPDATE SET {col} = stats_hourly.{col} + 1, spend = stats_hourly.spend + $5
        """,
        campaign_id, line_item_id, creative_id, supply_partner_id, spend,
    )


@router.post("/bid/{endpoint_key}")
async def bid(endpoint_key: str, request: Request):
    try:
        req = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid BidRequest JSON")
    result = await process_bid_request(endpoint_key, req)
    if result is None:
        return Response(status_code=204)
    return result


async def process_bid_request(endpoint_key: str, req: dict):
    """Core bid logic. Returns a BidResponse dict or None (no-bid).
    Raises HTTPException(404) for unknown endpoint keys."""
    pool = await get_pool()
    partner = await pool.fetchrow(
        "SELECT * FROM supply_partners WHERE endpoint_key = $1 AND status = 'active'", endpoint_key
    )
    if not partner:
        raise HTTPException(status_code=404, detail="Unknown endpoint")

    req_id = req.get("id") or str(uuid.uuid4())
    imps = req.get("imp") or []
    if not imps:
        return None

    device = req.get("device") or {}
    geo = (device.get("geo") or {})
    site = req.get("site") or {}
    device_id = device.get("ifa") or device.get("ip") or ""
    allowed_types = partner["allowed_ad_types"]
    if isinstance(allowed_types, str):
        allowed_types = json.loads(allowed_types)

    # Candidate line items: active campaign within budget & dates, active LI, approved creative
    candidates = await pool.fetch(
        """
        SELECT li.id AS li_id, li.bid_price, li.bid_type, li.max_bid, li.daily_budget AS li_budget,
               li.targeting, li.campaign_id,
               c.daily_budget AS camp_budget, c.frequency_cap_impressions, c.frequency_cap_period_hours,
               c.pacing, c.advertiser_id, adv.domain AS adv_domain,
               cr.id AS creative_id, cr.type, cr.width, cr.height, cr.asset_url, cr.click_url,
               cr.html_snippet, cr.native_title, cr.native_description, cr.native_cta,
               cr.native_icon_url, cr.video_url, cr.video_duration, cr.vast_xml, cr.name
        FROM line_items li
        JOIN campaigns c ON c.id = li.campaign_id AND c.status = 'active'
             AND (c.start_date IS NULL OR c.start_date <= CURRENT_DATE)
             AND (c.end_date IS NULL OR c.end_date >= CURRENT_DATE)
             AND (c.total_budget <= 0 OR c.spent_total < c.total_budget)
        JOIN advertisers adv ON adv.id = c.advertiser_id AND adv.status = 'active' AND adv.balance > 0
        JOIN creatives cr ON cr.campaign_id = c.id AND cr.status = 'approved'
             AND (cr.line_item_id IS NULL OR cr.line_item_id = li.id)
        WHERE li.status = 'active' AND li.bid_price > 0
        """
    )
    if not candidates:
        return None

    # Optimizer block lists — any dimension the platform observes can be blocked
    camp_ids = list({c["campaign_id"] for c in candidates})
    block_rows = await pool.fetch(
        "SELECT campaign_id, block_type, block_value FROM optimizer_blocks WHERE campaign_id = ANY($1)",
        camp_ids,
    )
    blocks: dict = {}
    for b in block_rows:
        blocks.setdefault(str(b["campaign_id"]), set()).add((b["block_type"], b["block_value"].lower()))

    from datetime import datetime, timezone as _tz
    req_domain = ((req.get("site") or req.get("app") or {}).get("domain")
                  or (req.get("app") or {}).get("bundle") or "").lower()
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
            if not _match_targeting(targeting, req):
                continue
            if not _match_imp_targeting(targeting, imp):
                continue
            price = float(c["bid_price"])
            if c["max_bid"]:
                price = min(price, float(c["max_bid"]))
            if price < floor:
                continue
            if best is None or price > best[0]:
                c["_deal_id"] = _matched_deal(targeting, imp)
                best = (price, c)

        if not best:
            continue

        price, c = best
        cost = price / 1000.0  # CPM -> cost per impression

        # Frequency cap
        if c["frequency_cap_impressions"]:
            ok = await check_frequency_cap(
                str(c["campaign_id"]), device_id,
                c["frequency_cap_impressions"], c["frequency_cap_period_hours"] or 24,
            )
            if not ok:
                continue

        # Even pacing: cap the effective daily budget by the share of day elapsed
        effective_budget = float(c["camp_budget"])
        if (c.get("pacing") or "asap") == "even" and effective_budget > 0:
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc)
            day_fraction = (now_utc.hour * 3600 + now_utc.minute * 60 + now_utc.second) / 86400
            # Allow a small burst ahead of schedule (10%) so delivery never stalls at 00:00
            effective_budget = effective_budget * min(1.0, day_fraction + 0.1)

        # Atomic budget reservation: campaign level, then line item level
        if not await reserve_budget("camp", str(c["campaign_id"]), cost, effective_budget):
            continue
        if float(c["li_budget"]) > 0:
            if not await reserve_budget("li", str(c["li_id"]), cost, float(c["li_budget"])):
                continue

        bid_id = str(uuid.uuid4())
        nurl = f"{base}/api/rtb/win?bid={bid_id}&price=${{AUCTION_PRICE}}"
        adm = _build_adm(c, base, bid_id)

        # Remember device for frequency counting at impression time
        if device_id and c["frequency_cap_impressions"]:
            r = get_redis()
            await r.set(
                f"bidfreq:{bid_id}",
                f"{device_id}|{c['frequency_cap_period_hours'] or 24}",
                ex=3600,
            )

        await _log_event(
            pool, "bid", bid_id=bid_id, imp_id=imp.get("id"),
            campaign_id=c["campaign_id"], line_item_id=c["li_id"], creative_id=c["creative_id"],
            supply_partner_id=partner["id"], price=price,
            device_type=DEVICE_TYPES.get(device.get("devicetype", 0), "unknown"),
            geo_country=geo.get("country"), ad_type=ad_type, domain=site.get("domain"),
            os=req_os, browser=req_browser,
        )
        await _bump_stats(pool, "bids", 0, c["campaign_id"], c["li_id"], c["creative_id"], partner["id"])

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
        seatbids.append({"bid": [bid_obj], "seat": "centraldsp"})

    if not seatbids:
        return None

    return {
        "id": req_id,
        "bidid": str(uuid.uuid4()),
        "cur": "USD",
        "seatbid": seatbids,
    }


async def _find_bid(pool, bid_id: str):
    return await pool.fetchrow(
        "SELECT * FROM bid_events WHERE bid_id = $1 AND event_type = 'bid' ORDER BY id LIMIT 1", bid_id
    )


@router.get("/win")
async def win_notice(bid: str, price: float | None = None):
    pool = await get_pool()
    if not await mark_once("win", bid):
        return {"ok": True, "duplicate": True}
    row = await _find_bid(pool, bid)
    if not row:
        raise HTTPException(status_code=404, detail="Unknown bid")
    clear_price = price if price is not None else float(row["price"] or 0)
    cost = clear_price / 1000.0
    await _log_event(
        pool, "win", bid_id=bid, imp_id=row["imp_id"], campaign_id=row["campaign_id"],
        line_item_id=row["line_item_id"], creative_id=row["creative_id"],
        supply_partner_id=row["supply_partner_id"], price=float(row["price"] or 0),
        clear_price=clear_price, device_type=row["device_type"],
        geo_country=row["geo_country"], ad_type=row["ad_type"], domain=row["domain"],
                os=row["os"], browser=row["browser"],
    )
    await _bump_stats(pool, "wins", cost, row["campaign_id"], row["line_item_id"], row["creative_id"], row["supply_partner_id"])
    await pool.execute(
        "UPDATE campaigns SET spent_today = spent_today + $2, spent_total = spent_total + $2 WHERE id = $1",
        row["campaign_id"], cost,
    )
    await pool.execute(
        "UPDATE line_items SET spent_today = spent_today + $2 WHERE id = $1",
        row["line_item_id"], cost,
    )
    # Billing: deduct advertiser balance and write to the ledger
    adv = await pool.fetchrow(
        """
        UPDATE advertisers a SET balance = a.balance - $2, updated_at = now()
        FROM campaigns c
        WHERE c.id = $1 AND a.id = c.advertiser_id
        RETURNING a.id AS advertiser_id, a.user_id, a.balance
        """,
        row["campaign_id"], cost,
    )
    if adv:
        await pool.execute(
            """
            INSERT INTO transactions (user_id, advertiser_id, type, amount, balance_after, description)
            VALUES ($1, $2, 'spend', $3, $4, $5)
            """,
            adv["user_id"], adv["advertiser_id"], -cost, float(adv["balance"]),
            f"Win notice bid={bid}",
        )
        # Auto-pause campaigns when the advertiser runs out of money
        if float(adv["balance"]) <= 0:
            await pool.execute(
                "UPDATE campaigns SET status = 'paused', updated_at = now() WHERE advertiser_id = $1 AND status = 'active'",
                adv["advertiser_id"],
            )
    return {"ok": True}


@router.get("/imp")
async def impression(bid: str):
    pool = await get_pool()
    if not await mark_once("imp", bid):
        return Response(content=_PIXEL, media_type="image/gif")
    row = await _find_bid(pool, bid)
    if row:
        await _log_event(
            pool, "impression", bid_id=bid, imp_id=row["imp_id"], campaign_id=row["campaign_id"],
            line_item_id=row["line_item_id"], creative_id=row["creative_id"],
            supply_partner_id=row["supply_partner_id"], device_type=row["device_type"],
            geo_country=row["geo_country"], ad_type=row["ad_type"], domain=row["domain"],
                os=row["os"], browser=row["browser"],
        )
        await _bump_stats(pool, "impressions", 0, row["campaign_id"], row["line_item_id"], row["creative_id"], row["supply_partner_id"])
        # Count the impression against the frequency cap
        r = get_redis()
        meta = await r.get(f"bidfreq:{bid}")
        if meta:
            device_id, _, period = meta.partition("|")
            await bump_frequency(str(row["campaign_id"]), device_id, int(period or 24))
    return Response(content=_PIXEL, media_type="image/gif")


_PIXEL = bytes.fromhex("47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b")


@router.get("/click")
async def click(bid: str, r: str = ""):
    pool = await get_pool()
    row = None
    if await mark_once("click", bid):
        row = await _find_bid(pool, bid)
        if row:
            await _log_event(
                pool, "click", bid_id=bid, imp_id=row["imp_id"], campaign_id=row["campaign_id"],
                line_item_id=row["line_item_id"], creative_id=row["creative_id"],
                supply_partner_id=row["supply_partner_id"], device_type=row["device_type"],
                geo_country=row["geo_country"], ad_type=row["ad_type"], domain=row["domain"],
                os=row["os"], browser=row["browser"],
            )
            await _bump_stats(pool, "clicks", 0, row["campaign_id"], row["line_item_id"], row["creative_id"], row["supply_partner_id"])
    # Redirect target: explicit r param, else fall back to the creative's click_url
    if not (r and r.startswith(("http://", "https://"))):
        if row is None:
            row = await _find_bid(pool, bid)
        if row and row["creative_id"]:
            r = await pool.fetchval("SELECT click_url FROM creatives WHERE id = $1", row["creative_id"]) or ""
    if r and r.startswith(("http://", "https://")):
        # Pass our click id to the landing page so the Central Pixel can attribute conversions
        sep = "&" if "?" in r else "?"
        return Response(status_code=302, headers={"Location": f"{r}{sep}cdsp_clid={bid}"})
    return {"ok": True}
