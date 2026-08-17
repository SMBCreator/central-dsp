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

from cache import check_frequency_cap, mark_once, reserve_budget
from db import get_pool

router = APIRouter(prefix="/rtb", tags=["rtb"])

DEVICE_TYPES = {1: "mobile", 2: "desktop", 3: "ctv", 4: "phone", 5: "tablet", 6: "connected", 7: "settopbox"}


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

    return True


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
                                supply_partner_id, price, clear_price, device_type, geo_country, ad_type, domain)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        """,
        event_type, kw.get("bid_id", ""), kw.get("imp_id"), kw.get("campaign_id"),
        kw.get("line_item_id"), kw.get("creative_id"), kw.get("supply_partner_id"),
        kw.get("price"), kw.get("clear_price"), kw.get("device_type"),
        kw.get("geo_country"), kw.get("ad_type"), kw.get("domain"),
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
               cr.id AS creative_id, cr.type, cr.width, cr.height, cr.asset_url, cr.click_url,
               cr.html_snippet, cr.native_title, cr.native_description, cr.native_cta,
               cr.native_icon_url, cr.video_url, cr.video_duration, cr.vast_xml, cr.name
        FROM line_items li
        JOIN campaigns c ON c.id = li.campaign_id AND c.status = 'active'
             AND (c.start_date IS NULL OR c.start_date <= CURRENT_DATE)
             AND (c.end_date IS NULL OR c.end_date >= CURRENT_DATE)
        JOIN creatives cr ON cr.campaign_id = c.id AND cr.status = 'approved'
             AND (cr.line_item_id IS NULL OR cr.line_item_id = li.id)
        WHERE li.status = 'active' AND li.bid_price > 0
        """
    )
    if not candidates:
        return None

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
            if not _match_targeting(targeting, req):
                continue
            price = float(c["bid_price"])
            if c["max_bid"]:
                price = min(price, float(c["max_bid"]))
            if price < floor:
                continue
            if best is None or price > best[0]:
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

        # Atomic budget reservation: campaign level, then line item level
        if not await reserve_budget("camp", str(c["campaign_id"]), cost, float(c["camp_budget"])):
            continue
        if float(c["li_budget"]) > 0:
            if not await reserve_budget("li", str(c["li_id"]), cost, float(c["li_budget"])):
                continue

        bid_id = str(uuid.uuid4())
        nurl = f"{base}/api/rtb/win?bid={bid_id}&price=${{AUCTION_PRICE}}"
        adm = _build_adm(c, base, bid_id)

        await _log_event(
            pool, "bid", bid_id=bid_id, imp_id=imp.get("id"),
            campaign_id=c["campaign_id"], line_item_id=c["li_id"], creative_id=c["creative_id"],
            supply_partner_id=partner["id"], price=price,
            device_type=DEVICE_TYPES.get(device.get("devicetype", 0), "unknown"),
            geo_country=geo.get("country"), ad_type=ad_type, domain=site.get("domain"),
        )
        await _bump_stats(pool, "bids", 0, c["campaign_id"], c["li_id"], c["creative_id"], partner["id"])

        seatbids.append({
            "bid": [{
                "id": bid_id,
                "impid": imp.get("id"),
                "price": round(price, 4),
                "nurl": nurl,
                "adm": adm,
                "crid": str(c["creative_id"]),
                "cid": str(c["campaign_id"]),
                "adomain": [],
                "w": c.get("width"),
                "h": c.get("height"),
            }],
            "seat": "centraldsp",
        })

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
        )
        await _bump_stats(pool, "impressions", 0, row["campaign_id"], row["line_item_id"], row["creative_id"], row["supply_partner_id"])
    return Response(content=_PIXEL, media_type="image/gif")


_PIXEL = bytes.fromhex("47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b")


@router.get("/click")
async def click(bid: str, r: str = ""):
    pool = await get_pool()
    if await mark_once("click", bid):
        row = await _find_bid(pool, bid)
        if row:
            await _log_event(
                pool, "click", bid_id=bid, imp_id=row["imp_id"], campaign_id=row["campaign_id"],
                line_item_id=row["line_item_id"], creative_id=row["creative_id"],
                supply_partner_id=row["supply_partner_id"], device_type=row["device_type"],
                geo_country=row["geo_country"], ad_type=row["ad_type"], domain=row["domain"],
            )
            await _bump_stats(pool, "clicks", 0, row["campaign_id"], row["line_item_id"], row["creative_id"], row["supply_partner_id"])
    if r and r.startswith(("http://", "https://")):
        return Response(status_code=302, headers={"Location": r})
    return {"ok": True}
