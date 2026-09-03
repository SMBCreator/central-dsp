"""Central Pixel — first-party analytics (umami-class) wired into the DSP.

GET  /pixel/script/{pixel_id}.js  <- tracking script for the client site
POST /pixel/collect               <- event ingestion (pageview / event / heartbeat)
CRUD /pixel/*                     <- pixels + goals management
GET  /pixel/{id}/stats            <- traffic quality overview
"""
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

import pixel_guard
from audiences import process_pixel_event
from auth import current_user
from rbac import require_campaign_write
from db import get_pool

router = APIRouter(prefix="/pixel", tags=["pixel"])


def _client_ip(request: Request) -> str | None:
    """IP клиента за nginx/Vercel: первый адрес из X-Forwarded-For, иначе peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first[:64]
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()[:64]
    return request.client.host if request.client else None

# ---------------------------------------------------------------------------
# Tracking script (served to client sites)
# ---------------------------------------------------------------------------

_SCRIPT_TEMPLATE = """(function(){
  var PID = "__PIXEL_ID__";
  var API = "__API_BASE__/pixel/collect";
  var SKEY = "cdsp_sid_" + PID, CKEY = "cdsp_clid";
  function sid(){ var s = localStorage.getItem(SKEY);
    if(!s){ s = Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem(SKEY, s); }
    return s; }
  // capture click id from landing URL (?cdsp_clid=...)
  try { var m = location.search.match(/[?&]cdsp_clid=([^&]+)/);
    if(m) localStorage.setItem(CKEY, decodeURIComponent(m[1])); } catch(e){}
  var maxScroll = 0;
  function measureScroll(){
    try {
      var doc = document.documentElement;
      var total = Math.max(doc.scrollHeight - doc.clientHeight, 1);
      var pct = Math.min(Math.round((window.scrollY || doc.scrollTop || 0) / total * 100), 100);
      if(pct > maxScroll) maxScroll = pct;
    } catch(e){}
  }
  window.addEventListener("scroll", measureScroll, { passive: true });
  function send(type, name, dur){
    try { navigator.sendBeacon(API, JSON.stringify({
      pid: PID, sid: sid(), type: type, name: name || null,
      url: location.href, ref: document.referrer || null,
      dur: dur || 0, sp: maxScroll, clid: localStorage.getItem(CKEY) || null,
      sw: screen.width, ua: navigator.userAgent, lang: navigator.language
    })); } catch(e){}
  }
  send("pageview");
  var t0 = Date.now(), hb = 15;
  var iv = setInterval(function(){
    if(document.visibilityState === "visible"){ measureScroll(); send("heartbeat", null, hb); }
  }, hb * 1000);
  window.addEventListener("pagehide", function(){
    measureScroll();
    send("heartbeat", null, Math.min(Math.round((Date.now()-t0)/1000) % hb, hb));
  });
  window.cdsp = { track: function(name){ send("event", name); } };
})();"""


@router.get("/script/{pixel_id}.js")
async def tracking_script(pixel_id: str, request: Request):
    pool = await get_pool()
    exists = await pool.fetchval("SELECT 1 FROM pixels WHERE id = $1", pixel_id)
    if not exists:
        raise HTTPException(status_code=404)
    base = str(request.base_url).rstrip("/")
    js = _SCRIPT_TEMPLATE.replace("__PIXEL_ID__", pixel_id).replace("__API_BASE__", base)
    return Response(content=js, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600", "Access-Control-Allow-Origin": "*"})


# ---------------------------------------------------------------------------
# Event ingestion + conversion matching
# ---------------------------------------------------------------------------

def _parse_device(ua: str, sw: int) -> tuple[str, str, str]:
    """Very light UA parse: (device_type, browser, os)."""
    ua_l = (ua or "").lower()
    if "ipad" in ua_l or (sw and 600 <= sw <= 1100 and "mobile" in ua_l):
        device = "tablet"
    elif "mobi" in ua_l or "android" in ua_l and "mobile" in ua_l:
        device = "phone"
    elif "smart-tv" in ua_l or "smarttv" in ua_l:
        device = "ctv"
    else:
        device = "desktop"
    browser = ("yandex" if "yabrowser" in ua_l else "edge" if "edg/" in ua_l
               else "opera" if "opr/" in ua_l else "chrome" if "chrome" in ua_l
               else "safari" if "safari" in ua_l else "firefox" if "firefox" in ua_l else "other")
    os_name = ("android" if "android" in ua_l else "ios" if ("iphone" in ua_l or "ipad" in ua_l)
               else "windows" if "windows" in ua_l else "macos" if "mac os" in ua_l
               else "linux" if "linux" in ua_l else "other")
    return device, browser, os_name


async def _match_goals(pool, pixel_id: str, session_id: str, event_type: str,
                       event_name: str | None, url: str | None, clid: str | None) -> None:
    """Check active goals for this pixel against the incoming event and record conversions."""
    goals = await pool.fetch("SELECT * FROM goals WHERE pixel_id = $1", pixel_id)
    if not goals:
        return
    for g in goals:
        hit = False
        if g["goal_type"] == "event" and event_type == "event" and event_name == g["match_value"]:
            hit = True
        elif g["goal_type"] == "url" and event_type == "pageview" and url and g["match_value"] in url:
            hit = True
        elif g["goal_type"] == "duration" and event_type == "heartbeat":
            total = await pool.fetchval(
                "SELECT COALESCE(SUM(duration_sec),0) FROM pixel_events WHERE pixel_id = $1 AND session_id = $2",
                pixel_id, session_id,
            )
            try:
                if int(total) >= int(g["match_value"]):
                    hit = True
            except (ValueError, TypeError):
                continue
        if not hit:
            continue
        # Attribute to the bid (campaign / SSP / domain / device) via clid
        attrib = {}
        if clid:
            row = await pool.fetchrow(
                """SELECT campaign_id, line_item_id, creative_id, supply_partner_id, domain, device_type, geo_country
                   FROM bid_events WHERE bid_id = $1 AND event_type = 'bid' LIMIT 1""",
                clid,
            )
            if row:
                attrib = dict(row)
        inserted = await pool.fetchrow(
            """INSERT INTO conversions (goal_id, pixel_id, session_id, clid, campaign_id, line_item_id,
                                        creative_id, supply_partner_id, domain, device_type, geo_country, value)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
               ON CONFLICT (goal_id, session_id) DO NOTHING RETURNING id""",
            g["id"], pixel_id, session_id, clid,
            attrib.get("campaign_id"), attrib.get("line_item_id"), attrib.get("creative_id"),
            attrib.get("supply_partner_id"), attrib.get("domain"), attrib.get("device_type"),
            attrib.get("geo_country"), float(g["value"] or 0),
        )
        # bump hourly conversions for CPA math
        if inserted and attrib.get("campaign_id"):
            await pool.execute(
                """INSERT INTO stats_hourly (ts_hour, campaign_id, line_item_id, creative_id, supply_partner_id, conversions)
                   VALUES (date_trunc('hour', now()), $1, $2, $3, $4, 1)
                   ON CONFLICT (ts_hour, campaign_id, line_item_id, creative_id, supply_partner_id)
                   DO UPDATE SET conversions = stats_hourly.conversions + 1""",
                attrib["campaign_id"], attrib.get("line_item_id"), attrib.get("creative_id"),
                attrib.get("supply_partner_id"),
            )


@router.post("/collect")
async def collect(request: Request):
    try:
        body = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return Response(status_code=204)
    pixel_id = str(body.get("pid") or "")
    session_id = str(body.get("sid") or "")[:64]
    event_type = str(body.get("type") or "pageview")
    if event_type not in ("pageview", "event", "heartbeat") or not pixel_id or not session_id:
        return Response(status_code=204)
    pool = await get_pool()
    try:
        uuid.UUID(pixel_id)
    except ValueError:
        return Response(status_code=204)
    if not await pool.fetchval("SELECT 1 FROM pixels WHERE id = $1", pixel_id):
        return Response(status_code=204)

    device, browser, os_name = _parse_device(str(body.get("ua") or ""), int(body.get("sw") or 0))
    event_name = (str(body.get("name"))[:120] if body.get("name") else None)
    url = (str(body.get("url"))[:500] if body.get("url") else None)
    clid = (str(body.get("clid"))[:64] if body.get("clid") else None)

    # Дедуп/антифлуд (R10): дубли sendBeacon, зациклившийся track(), боты с новыми sid.
    # Отвечаем всё равно 204 — скрипту на сайте клиента знать причину не нужно.
    drop = await pixel_guard.should_accept(
        pixel_id=pixel_id, session_id=session_id, event_type=event_type,
        event_name=event_name, url=url, client_ip=_client_ip(request),
    )
    if drop:
        return Response(status_code=204, headers={"Access-Control-Allow-Origin": "*", "X-Pixel-Drop": drop})
    dur = min(max(int(body.get("dur") or 0), 0), 60)
    try:
        scroll_pct = min(max(int(body.get("sp") or 0), 0), 100)
    except (ValueError, TypeError):
        scroll_pct = 0

    await pool.execute(
        """INSERT INTO pixel_events (pixel_id, session_id, event_type, event_name, url, referrer,
                                     device_type, browser, os, duration_sec, scroll_pct, clid)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
        pixel_id, session_id, event_type, event_name, url,
        (str(body.get("ref"))[:500] if body.get("ref") else None),
        device, browser, os_name, dur, scroll_pct, clid,
    )
    await _match_goals(pool, pixel_id, session_id, event_type, event_name, url, clid)
    await process_pixel_event(pool, pixel_id, clid, event_type, event_name, url)
    return Response(status_code=204, headers={"Access-Control-Allow-Origin": "*"})


@router.options("/collect")
async def collect_options():
    return Response(status_code=204, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    })


# ---------------------------------------------------------------------------
# Management API (authenticated)
# ---------------------------------------------------------------------------

class PixelIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=3, max_length=255)


class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal_type: str = Field(pattern="^(event|url|duration)$")
    match_value: str = Field(min_length=1, max_length=255)
    value: float = Field(default=0, ge=0)


@router.get("s")
async def list_pixels(user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT p.*,
                  (SELECT count(*) FROM goals g WHERE g.pixel_id = p.id) AS goals_count,
                  (SELECT count(DISTINCT session_id) FROM pixel_events e
                    WHERE e.pixel_id = p.id AND e.created_at >= now() - interval '24 hours') AS sessions_24h,
                  (SELECT count(*) FROM conversions c
                    WHERE c.pixel_id = p.id AND c.created_at >= now() - interval '24 hours') AS conversions_24h
           FROM pixels p WHERE p.user_id = $1 ORDER BY p.created_at DESC""",
        user["id"],
    )
    return [dict(r) for r in rows]


@router.post("s")
async def create_pixel(data: PixelIn, user: dict = Depends(require_campaign_write)):
    pool = await get_pool()
    row = await pool.fetchrow(
        "INSERT INTO pixels (user_id, name, domain) VALUES ($1,$2,$3) RETURNING *",
        user["id"], data.name, data.domain.lower().strip(),
    )
    return dict(row)


@router.delete("s/{pixel_id}")
async def delete_pixel(pixel_id: str, user: dict = Depends(require_campaign_write)):
    pool = await get_pool()
    deleted = await pool.fetchval(
        "DELETE FROM pixels WHERE id = $1 AND user_id = $2 RETURNING id", pixel_id, user["id"]
    )
    if not deleted:
        raise HTTPException(status_code=404)
    return {"ok": True}


@router.get("s/{pixel_id}/goals")
async def list_goals(pixel_id: str, user: dict = Depends(current_user)):
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT g.*, (SELECT count(*) FROM conversions c WHERE c.goal_id = g.id) AS conversions_total
           FROM goals g JOIN pixels p ON p.id = g.pixel_id
           WHERE g.pixel_id = $1 AND p.user_id = $2 ORDER BY g.created_at""",
        pixel_id, user["id"],
    )
    return [dict(r) for r in rows]


@router.post("s/{pixel_id}/goals")
async def create_goal(pixel_id: str, data: GoalIn, user: dict = Depends(require_campaign_write)):
    pool = await get_pool()
    owned = await pool.fetchval("SELECT 1 FROM pixels WHERE id = $1 AND user_id = $2", pixel_id, user["id"])
    if not owned:
        raise HTTPException(status_code=404)
    row = await pool.fetchrow(
        "INSERT INTO goals (pixel_id, name, goal_type, match_value, value) VALUES ($1,$2,$3,$4,$5) RETURNING *",
        pixel_id, data.name, data.goal_type, data.match_value, data.value,
    )
    return dict(row)


@router.delete("s/{pixel_id}/goals/{goal_id}")
async def delete_goal(pixel_id: str, goal_id: str, user: dict = Depends(require_campaign_write)):
    pool = await get_pool()
    deleted = await pool.fetchval(
        """DELETE FROM goals g USING pixels p
           WHERE g.id = $1 AND g.pixel_id = $2 AND p.id = g.pixel_id AND p.user_id = $3 RETURNING g.id""",
        goal_id, pixel_id, user["id"],
    )
    if not deleted:
        raise HTTPException(status_code=404)
    return {"ok": True}


@router.get("s/{pixel_id}/stats")
async def pixel_stats(pixel_id: str, hours: int = 168, user: dict = Depends(current_user)):
    """Traffic quality: sessions, engagement, conversions by source."""
    hours = max(1, min(hours, 720))
    pool = await get_pool()
    owned = await pool.fetchval("SELECT 1 FROM pixels WHERE id = $1 AND user_id = $2", pixel_id, user["id"])
    if not owned:
        raise HTTPException(status_code=404)
    totals = await pool.fetchrow(
        """SELECT count(DISTINCT session_id) AS sessions,
                  count(*) FILTER (WHERE event_type = 'pageview') AS pageviews,
                  COALESCE(SUM(duration_sec),0) AS total_time,
                  count(DISTINCT session_id) FILTER (WHERE clid IS NOT NULL) AS ad_sessions,
                  COALESCE(AVG(scroll_pct) FILTER (WHERE event_type = 'heartbeat'), 0) AS avg_scroll
           FROM pixel_events
           WHERE pixel_id = $1 AND created_at >= now() - ($2 || ' hours')::interval""",
        pixel_id, str(hours),
    )
    # Отказы: сессии с одним pageview, без событий и суммарным временем < 10 сек
    behavior = await pool.fetchrow(
        """SELECT count(*) FILTER (WHERE pv = 1 AND ev = 0 AND dur < 10) AS bounces, count(*) AS total
           FROM (
             SELECT session_id,
                    count(*) FILTER (WHERE event_type = 'pageview') AS pv,
                    count(*) FILTER (WHERE event_type = 'event') AS ev,
                    COALESCE(SUM(duration_sec),0) AS dur
             FROM pixel_events
             WHERE pixel_id = $1 AND created_at >= now() - ($2 || ' hours')::interval
             GROUP BY session_id
           ) s""",
        pixel_id, str(hours),
    )
    conv = await pool.fetchval(
        "SELECT count(*) FROM conversions WHERE pixel_id = $1 AND created_at >= now() - ($2 || ' hours')::interval",
        pixel_id, str(hours),
    )
    by_source = await pool.fetch(
        """SELECT COALESCE(c.domain, 'organic') AS source,
                  count(*) AS conversions,
                  COALESCE(SUM(c.value),0) AS value
           FROM conversions c
           WHERE c.pixel_id = $1 AND c.created_at >= now() - ($2 || ' hours')::interval
           GROUP BY 1 ORDER BY 2 DESC LIMIT 20""",
        pixel_id, str(hours),
    )
    sessions = int(totals["sessions"] or 0)
    pageviews = int(totals["pageviews"] or 0)
    total_b = int(behavior["total"] or 0)
    return {
        "sessions": sessions,
        "pageviews": pageviews,
        "avg_session_sec": round(float(totals["total_time"] or 0) / sessions, 1) if sessions else 0,
        "ad_sessions": int(totals["ad_sessions"] or 0),
        "conversions": int(conv or 0),
        # Поведенческие метрики (фундамент скоринга качества площадок)
        "avg_depth": round(pageviews / sessions, 2) if sessions else 0,
        "bounce_rate": round(int(behavior["bounces"] or 0) / total_b * 100, 1) if total_b else 0,
        "avg_scroll_pct": round(float(totals["avg_scroll"] or 0), 1),
        "by_source": [dict(r) for r in by_source],
    }
