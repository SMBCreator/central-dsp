"""OpenRTB-совместимость (audit R-04): PMP-сделки, native assets по запросу,
video MIME/protocol/duration.

Это эталон; Go-порт — bidder-go/internal/rtb/openrtb.go. Обе стороны гоняют
одни фикстуры bidder-go/testdata/openrtb/*.json (tests/test_openrtb_compat.py
и TestOpenRTB_* в Go).

Принцип: неподдерживаемый запрос -> явный отказ с причиной в
rtb_openrtb_incompat_total{reason}, а не заведомо невалидная ставка.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit

# Причины (метки метрики; те же строки в Go).
INCOMPAT_NATIVE_MALFORMED = "native_request_malformed"
INCOMPAT_NATIVE_ASSET = "native_asset_missing"
INCOMPAT_VIDEO_MIME = "video_mime"
INCOMPAT_VIDEO_PROTOCOL = "video_protocol"
INCOMPAT_VIDEO_DURATION = "video_duration"
INCOMPAT_PRIVATE_AUCTION = "private_auction"


class NativeRequestError(ValueError):
    """imp.native.request не разбирается."""


# ---------- Native (OpenRTB Native 1.1/1.2) ----------

def parse_native_request(native: dict | None) -> dict | None:
    """imp.native.request -> {"assets": [...]} или None (запроса нет).

    По спецификации request — строка JSON; некоторые SSP шлют объект.
    Допускается обёртка {"native": {...}}. Мусор -> NativeRequestError.
    """
    if not native:
        return None
    raw = native.get("request")
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError as e:
            raise NativeRequestError(str(e)) from e
    elif isinstance(raw, dict):
        parsed = raw
    else:
        raise NativeRequestError("request is neither string nor object")
    if not isinstance(parsed, dict):
        raise NativeRequestError("request is not an object")
    if isinstance(parsed.get("native"), dict):
        parsed = parsed["native"]
    assets = parsed.get("assets")
    if assets is not None and not isinstance(assets, list):
        raise NativeRequestError("assets is not a list")
    return {"assets": assets or []}


def _trunc(s: str, n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    return s if n <= 0 else s[:n]


def build_native_assets(req: dict, creative: dict) -> list[dict] | None:
    """assets ответа ровно по запрошенным id/типам.

    None — обязательный asset нечем заполнить (или нечем заполнить ничего).
    """
    cta = creative.get("native_cta") or "Подробнее"
    out: list[dict] = []
    for a in req.get("assets") or []:
        if not isinstance(a, dict):
            continue
        body: dict | None = None
        if isinstance(a.get("title"), dict):
            title = creative.get("native_title") or ""
            if title:
                body = {"title": {"text": _trunc(title, a["title"].get("len"))}}
        elif isinstance(a.get("img"), dict):
            itype = int(a["img"].get("type") or 0)
            url = ""
            if itype == 3:
                url = creative.get("asset_url") or ""
            elif itype in (1, 2):
                url = creative.get("native_icon_url") or ""
            if url:
                img: dict = {"url": url}
                w, h = creative.get("width"), creative.get("height")
                if itype == 3 and w and h:
                    img["w"], img["h"] = int(w), int(h)
                body = {"img": img}
        elif isinstance(a.get("data"), dict):
            dtype = int(a["data"].get("type") or 0)
            val = ""
            if dtype == 2:
                val = creative.get("native_description") or ""
            elif dtype == 12:
                val = cta
            elif dtype == 1:
                val = creative.get("adv_name") or ""
            if val:
                body = {"data": {"value": _trunc(val, a["data"].get("len"))}}
        if body is None:
            if int(a.get("required") or 0) == 1:
                return None
            continue
        body["id"] = a.get("id")
        out.append(body)
    return out or None


# ---------- Video ----------

_RE_MEDIA_TYPE = re.compile(r'<MediaFile[^>]*\btype="([^"]+)"')
_RE_VAST_VERSION = re.compile(r'<VAST[^>]*\bversion="(\d)')

_EXT_MIME = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
    ".mov": "video/quicktime", ".m3u8": "application/x-mpegURL",
    ".mpd": "application/dash+xml", ".3gp": "video/3gpp", ".ogv": "video/ogg",
}


def creative_video_mime(creative: dict) -> str:
    """MIME ролика: первый MediaFile из vast_xml, иначе расширение video_url,
    иначе video/mp4 (так рендерит наш VAST)."""
    vast = creative.get("vast_xml") or ""
    if vast:
        m = _RE_MEDIA_TYPE.search(vast)
        if m:
            return m.group(1)
    path = urlsplit(creative.get("video_url") or "").path
    return _EXT_MIME.get(os.path.splitext(path)[1].lower(), "video/mp4")


def creative_vast_protocols(creative: dict) -> list[int]:
    """imp.video.protocols, которые обслуживает креатив (§5.8: 2/5 VAST 2,
    3/6 VAST 3, 7/8 VAST 4). Наш генератор — 3.0."""
    ver = "3"
    vast = creative.get("vast_xml") or ""
    if vast:
        m = _RE_VAST_VERSION.search(vast)
        if m:
            ver = m.group(1)
    return {"2": [2, 5], "4": [7, 8]}.get(ver, [3, 6])


def creative_video_duration(creative: dict) -> int:
    """Длительность для min/maxduration; 0 = неизвестна (пользовательский VAST
    без video_duration не проверяем)."""
    d = creative.get("video_duration")
    if d and int(d) > 0:
        return int(d)
    if creative.get("vast_xml"):
        return 0
    return 15  # дефолт генератора VAST


def video_incompat(video: dict | None, creative: dict) -> str | None:
    """None — креатив совместим со слотом, иначе причина."""
    if not video:
        return None
    mimes = [str(m).strip().lower() for m in (video.get("mimes") or [])]
    if mimes and creative_video_mime(creative).lower() not in mimes:
        return INCOMPAT_VIDEO_MIME
    protocols = [int(p) for p in (video.get("protocols") or [])]
    if protocols and not set(protocols) & set(creative_vast_protocols(creative)):
        return INCOMPAT_VIDEO_PROTOCOL
    d = creative_video_duration(creative)
    if d > 0:
        vmin = int(video.get("minduration") or 0)
        vmax = int(video.get("maxduration") or 0)
        if vmin > 0 and d < vmin:
            return INCOMPAT_VIDEO_DURATION
        if vmax > 0 and d > vmax:
            return INCOMPAT_VIDEO_DURATION
    return None


def vast_duration(sec: int) -> str:
    """Секунды -> HH:MM:SS (VAST 3 §2.3.1.3)."""
    sec = max(int(sec or 0), 0)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


# ---------- PMP ----------

def matched_deal(imp: dict, deal_ids) -> dict | None:
    """Первая сделка imp.pmp.deals из настроенных в line item (порядок — deal_ids)."""
    wanted = [str(d) for d in (deal_ids or [])]
    deals = ((imp.get("pmp") or {}).get("deals") or [])
    if not wanted or not deals:
        return None
    by_id = {str(d.get("id")): d for d in deals if isinstance(d, dict)}
    for w in wanted:
        if w in by_id:
            return by_id[w]
    return None


def private_auction_only(imp: dict) -> bool:
    """imp.pmp.private_auction=1: торгуются только сделки (§3.2.11)."""
    return int(((imp.get("pmp") or {}).get("private_auction")) or 0) == 1
