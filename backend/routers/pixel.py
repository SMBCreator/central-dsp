"""Central Pixel — first-party analytics (umami-class) wired into the DSP.

GET  /pixel/script/{pixel_id}.js  <- tracking script for the client site
POST /pixel/collect               <- event ingestion (pageview / event / heartbeat)
CRUD /pixel/*                     <- pixels + goals management
GET  /pixel/{id}/stats            <- traffic quality overview
"""
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError, field_validator

import pixel_guard
from audiences import add_goal_members, process_pixel_event
from auth import current_user
import conversion_attribution as attribution
from rbac import require_campaign_write
from db import get_pool
from routers.settings import public_api_base_url

log = logging.getLogger("pixel")

router = APIRouter(prefix="/pixel", tags=["pixel"])

# Полезная нагрузка beacon'а — ~600 байт; всё, что больше 16 КБ, — не наш скрипт.
# Проверяется по Content-Length ДО чтения тела и ещё раз по факту (audit E-05).
MAX_COLLECT_BODY = 16 * 1024
# Окно атрибуции клик → конверсия: bid старше окна к кампании не привязывается.
ATTRIBUTION_WINDOW_DAYS = int(os.environ.get("PIXEL_ATTRIBUTION_WINDOW_DAYS", "30"))
# Насколько глубоко искать бид по clid, чтобы отличить out_of_window от unattributed.
ATTRIBUTION_LOOKUP_DAYS = max(90, ATTRIBUTION_WINDOW_DAYS)

_CORS = {"Access-Control-Allow-Origin": "*"}


def _noop(reason: str | None = None) -> Response:
    """204 на любой мусор: скрипту на сайте клиента причина не нужна, а
    отличать «принято» от «отброшено» снаружи нельзя (anti-enumeration)."""
    headers = dict(_CORS)
    if reason:
        headers["X-Pixel-Drop"] = reason
    return Response(status_code=204, headers=headers)


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


def render_script(pixel_id: str, api_base: str) -> str:
    return _SCRIPT_TEMPLATE.replace("__PIXEL_ID__", pixel_id).replace("__API_BASE__", api_base.rstrip("/"))


@router.get("/script/{pixel_id}.js")
async def tracking_script(pixel_id: str):
    try:
        uuid.UUID(pixel_id)
    except ValueError:
        raise HTTPException(status_code=404)
    # Адрес приёма — только канонический публичный URL API (E-05). request.base_url
    # за прокси даёт внутренний host или домен без /api, и beacon уходит мимо backend.
    api_base = public_api_base_url()
    if not api_base:
        log.error("pixel script requested but PUBLIC_API_BASE_URL/PUBLIC_BASE_URL are not set")
        raise HTTPException(status_code=404)
    pool = await get_pool()
    if not await pool.fetchval("SELECT 1 FROM pixels WHERE id = $1", pixel_id):
        raise HTTPException(status_code=404)
    return Response(content=render_script(pixel_id, api_base), media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600", **_CORS})


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


async def _attributed_bid(pool, pixel_owner_id: str, clid: str) -> dict | None:
    """Бид для атрибуции конверсии по clid (audit E-05).

    Привязываем только если кампания бида принадлежит владельцу пикселя (или его
    поддереву agency → client). Чужой clid → бид не найден → unattributed.
    Окно атрибуции здесь НЕ фильтр: бид ищется до ATTRIBUTION_LOOKUP_DAYS назад,
    а статус attributed/out_of_window выставляет conversion_attribution.classify
    (B14c) — иначе «вне окна» неотличимо от «без clid».
    """
    row = await pool.fetchrow(
        """
        WITH RECURSIVE owners AS (
            SELECT id FROM users WHERE id = $2
            UNION ALL
            SELECT u.id FROM users u JOIN owners o ON u.parent_user_id = o.id
        )
        SELECT be.campaign_id, be.line_item_id, be.creative_id, be.supply_partner_id,
               be.domain, be.device_type, be.geo_country, be.os, be.browser, be.ad_type,
               be.tagid, be.publisher_id, be.bundle, be.is_test, be.created_at
        FROM bid_events be
        JOIN campaigns c ON c.id = be.campaign_id
        WHERE be.bid_id = $1 AND be.event_type = 'bid'
          AND be.created_at >= now() - make_interval(days => $3)
          AND c.user_id IN (SELECT id FROM owners)
        ORDER BY be.created_at DESC
        LIMIT 1
        """,
        clid, pixel_owner_id, ATTRIBUTION_LOOKUP_DAYS,
    )
    return dict(row) if row else None


async def _match_goals(pool, pixel_id: str, pixel_owner_id: str, session_id: str, event_type: str,
                       event_name: str | None, url: str | None, clid: str | None,
                       external_id: str | None = None, raw_payload: dict | None = None) -> None:
    """Check active goals for this pixel against the incoming event and record conversions."""
    goals = await pool.fetch("SELECT * FROM goals WHERE pixel_id = $1", pixel_id)
    if not goals:
        return
    found = await _attributed_bid(pool, pixel_owner_id, clid) if clid else None
    now = datetime.now(UTC)
    status = attribution.classify(found, now, ATTRIBUTION_WINDOW_DAYS)
    # В conversions (и в hourly/CPA) бид попадает только при attributed; для
    # out_of_window измерения бида остаются в conversion_attributions.
    bid = found if status == attribution.STATUS_ATTRIBUTED else None
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
        # Attribute to the bid (campaign / SSP / domain / device) via clid —
        # только свой и в окне атрибуции (E-05), иначе organic.
        attrib = {k: v for k, v in bid.items() if k != "created_at"} if bid else {}
        # E-04: конверсия по тестовому биду (OpenRTB test=1) наследует флаг —
        # в журнале остаётся, в hourly/CPA/ML не попадает.
        is_test = bool(attrib.pop("is_test", False)) or bool((found or {}).get("is_test", False))
        inserted = await pool.fetchrow(
            """INSERT INTO conversions (goal_id, pixel_id, session_id, clid, campaign_id, line_item_id,
                                        creative_id, supply_partner_id, domain, device_type, geo_country, value,
                                        is_test)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
               ON CONFLICT (goal_id, session_id) DO NOTHING RETURNING id""",
            g["id"], pixel_id, session_id, clid,
            attrib.get("campaign_id"), attrib.get("line_item_id"), attrib.get("creative_id"),
            attrib.get("supply_partner_id"), attrib.get("domain"), attrib.get("device_type"),
            attrib.get("geo_country"), float(g["value"] or 0), is_test,
        )
        if inserted:
            await attribution.record(
                pool, conversion_id=inserted["id"], owner_user_id=pixel_owner_id, pixel_id=pixel_id,
                goal_id=g["id"], status=status, clid=clid, bid=found, converted_at=now,
                external_conversion_id=external_id, raw_payload=raw_payload, is_test=is_test,
            )
        # Goal segments (R-06): первая конверсия по цели наполняет сегменты
        # rule_type='goal' этого пикселя. После INSERT, чтобы дубль конверсии
        # не трогал аудиторию повторно; тестовый бид в аудиторию не попадает.
        if inserted and not is_test:
            await add_goal_members(pool, pixel_id, str(g["id"]), clid)
        # bump hourly conversions for CPA math
        if inserted and attrib.get("campaign_id") and not is_test:
            await pool.execute(
                """INSERT INTO stats_hourly (ts_hour, campaign_id, line_item_id, creative_id, supply_partner_id, conversions)
                   VALUES (date_trunc('hour', now()), $1, $2, $3, $4, 1)
                   ON CONFLICT (ts_hour, campaign_id, line_item_id, creative_id, supply_partner_id)
                   DO UPDATE SET conversions = stats_hourly.conversions + 1""",
                attrib["campaign_id"], attrib.get("line_item_id"), attrib.get("creative_id"),
                attrib.get("supply_partner_id"),
            )


class CollectIn(BaseModel):
    """Beacon от tracking-скрипта. Строгая схема до любой работы с БД (audit E-05):
    неверный тип поля / лишний размер / не-объект → 204 без побочных эффектов."""

    model_config = {"extra": "ignore", "str_max_length": 2048}

    pid: uuid.UUID
    sid: str = Field(min_length=1, max_length=64)
    type: Literal["pageview", "event", "heartbeat"] = "pageview"
    name: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=500)
    ref: str | None = Field(default=None, max_length=500)
    clid: str | None = Field(default=None, max_length=64)
    # внешний id конверсии (номер заказа) — сверка с CRM рекламодателя (F-02)
    oid: str | None = Field(default=None, max_length=128)
    dur: int = Field(default=0, ge=0, le=60)
    sp: int = Field(default=0, ge=0, le=100)
    sw: int = Field(default=0, ge=0, le=20000)
    ua: str = Field(default="", max_length=512)
    lang: str | None = Field(default=None, max_length=32)

    @field_validator("name", "url", "ref", "clid", "oid", "lang", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return None if v in ("", 0, False) else v

    @field_validator("dur", "sp", "sw", mode="before")
    @classmethod
    def _clamp_numbers(cls, v, info):
        """Скрипт может прислать float/строку/мусор — клэмпим, а не отвергаем весь beacon."""
        if v is None or isinstance(v, bool):
            return 0
        try:
            n = int(float(v))
        except (TypeError, ValueError):
            return 0
        hi = {"dur": 60, "sp": 100, "sw": 20000}[info.field_name]
        return min(max(n, 0), hi)


def parse_collect(raw: bytes) -> CollectIn | None:
    """bytes → CollectIn или None на любой мусор (не JSON, не объект, неверные типы)."""
    if len(raw) > MAX_COLLECT_BODY:
        return None
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    try:
        return CollectIn.model_validate(body)
    except ValidationError:
        return None


@router.post("/collect")
async def collect(request: Request):
    # 1) Размер — по заголовку, до чтения тела.
    cl = request.headers.get("content-length")
    if cl and (not cl.isdigit() or int(cl) > MAX_COLLECT_BODY):
        return _noop("too_large")
    raw = await request.body()
    # 2) Схема — до любой работы с БД/Redis.
    ev = parse_collect(raw)
    if ev is None:
        return _noop("invalid")
    pixel_id = str(ev.pid)

    # 3) Дедуп/антифлуд (R10) — до первого запроса в БД: боты с новыми sid и
    #    несуществующими pid не должны стоить нам SELECT'ов.
    drop = await pixel_guard.should_accept(
        pixel_id=pixel_id, session_id=ev.sid, event_type=ev.type,
        event_name=ev.name, url=ev.url, client_ip=_client_ip(request),
    )
    if drop:
        return _noop(drop)

    # 4) Только теперь — БД.
    pool = await get_pool()
    owner_id = await pool.fetchval("SELECT user_id FROM pixels WHERE id = $1", pixel_id)
    if not owner_id:
        return _noop("unknown_pixel")

    device, browser, os_name = _parse_device(ev.ua, ev.sw)
    await pool.execute(
        """INSERT INTO pixel_events (pixel_id, session_id, event_type, event_name, url, referrer,
                                     device_type, browser, os, duration_sec, scroll_pct, clid)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
        pixel_id, ev.sid, ev.type, ev.name, ev.url, ev.ref,
        device, browser, os_name, ev.dur, ev.sp, ev.clid,
    )
    await _match_goals(pool, pixel_id, str(owner_id), ev.sid, ev.type, ev.name, ev.url, ev.clid,
                       external_id=ev.oid,
                       raw_payload={"type": ev.type, "name": ev.name, "url": ev.url, "ref": ev.ref})
    await process_pixel_event(pool, pixel_id, ev.clid, ev.type, ev.name, ev.url)
    return _noop()


@router.options("/collect")
async def collect_options():
    return Response(status_code=204, headers={
        **_CORS,
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
                    WHERE c.pixel_id = p.id AND NOT c.is_test
                      AND c.created_at >= now() - interval '24 hours') AS conversions_24h
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
        """SELECT g.*, (SELECT count(*) FROM conversions c WHERE c.goal_id = g.id AND NOT c.is_test) AS conversions_total
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
        """SELECT count(*) FROM conversions
           WHERE pixel_id = $1 AND NOT is_test AND created_at >= now() - ($2 || ' hours')::interval""",
        pixel_id, str(hours),
    )
    by_source = await pool.fetch(
        """SELECT COALESCE(c.domain, 'organic') AS source,
                  count(*) AS conversions,
                  COALESCE(SUM(c.value),0) AS value
           FROM conversions c
           WHERE c.pixel_id = $1 AND NOT c.is_test AND c.created_at >= now() - ($2 || ' hours')::interval
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
