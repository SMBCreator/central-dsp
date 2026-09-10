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
from urllib.parse import quote, urlsplit
from xml.parsers import expat

# Причины (метки метрики; те же строки в Go).
INCOMPAT_NATIVE_MALFORMED = "native_request_malformed"
INCOMPAT_NATIVE_ASSET = "native_asset_missing"
INCOMPAT_VIDEO_MIME = "video_mime"
INCOMPAT_VIDEO_PROTOCOL = "video_protocol"
INCOMPAT_VIDEO_DURATION = "video_duration"
INCOMPAT_PRIVATE_AUCTION = "private_auction"
# Audit R-05: разметку не собрать безопасно (bidder-go/internal/rtb/adm.go admIncompat).
INCOMPAT_ADM_URL = "adm_url_invalid"
INCOMPAT_ADM_VAST = "adm_vast_malformed"


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


# ---------- adm safety (audit R-05) ----------
# Зеркало bidder-go/internal/rtb/adm.go (safeURL/admIncompat/substituteClickMacros)
# и vast.go (injectVastTrackers). Тот же слой используют Pydantic-валидаторы
# креативов: что принято на загрузке — соберётся в разметку в обоих биддерах.

MAX_ADM_URL_LEN = 2048
_URL_FORBIDDEN = set('"\'<>\\`')

CLICK_MACROS_RAW = ("${CLICK_URL}", "%%CLICK_URL_UNESC%%", "{CLICK_URL}", "[CLICK_URL]", "${CLICK_URL_UNESC}")
CLICK_MACROS_ENC = ("${CLICK_URL_ENC}", "%%CLICK_URL_ESC%%", "${CLICK_URL_ESC}", "{CLICK_URL_ENC}")

VAST_EVENTS = ("start", "firstQuartile", "midpoint", "thirdQuartile", "complete", "skip")


def safe_url(s) -> bool:
    """Абсолютный http(s) с хостом, <= MAX_ADM_URL_LEN, без пробелов, управляющих
    символов и кавычек/угловых скобок/обратного слэша (ломают контексты вставки
    и нужны для инъекций). Макросы SSP вида ${AUCTION_PRICE} допустимы."""
    if not isinstance(s, str) or not s or len(s) > MAX_ADM_URL_LEN:
        return False
    for ch in s:
        if ord(ch) <= 0x20 or ord(ch) == 0x7F or ch in _URL_FORBIDDEN:
            return False
    try:
        parts = urlsplit(s)
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc) and bool(parts.hostname)


def adm_incompat(creative: dict) -> str | None:
    """None — все URL креатива пригодны для разметки и VAST разбирается, иначе причина."""
    if not safe_url(creative.get("click_url")):
        return INCOMPAT_ADM_URL
    for k in ("imp_tracker_url", "click_tracker_url"):
        if creative.get(k) and not safe_url(creative.get(k)):
            return INCOMPAT_ADM_URL
    typ = creative.get("type")
    if typ == "banner":
        if not creative.get("html_snippet") and not safe_url(creative.get("asset_url")):
            return INCOMPAT_ADM_URL
    elif typ in ("native", "push"):
        for k in ("asset_url", "native_icon_url"):
            if creative.get(k) and not safe_url(creative.get(k)):
                return INCOMPAT_ADM_URL
    else:
        if creative.get("vast_xml"):
            try:
                if not _scan_vast(creative["vast_xml"]):
                    return INCOMPAT_ADM_VAST
            except ValueError:
                return INCOMPAT_ADM_VAST
        elif not safe_url(creative.get("video_url")):
            return INCOMPAT_ADM_URL
    return None


def substitute_click_macros(snippet: str, click_url: str) -> tuple[str, bool]:
    """Подставляет наш клик-URL вместо макросов в HTML5-сниппете; второе
    значение — был ли хоть один макрос."""
    had = False
    for m in CLICK_MACROS_RAW:
        if m in snippet:
            had = True
            snippet = snippet.replace(m, click_url)
    enc = quote(click_url, safe="")
    for m in CLICK_MACROS_ENC:
        if m in snippet:
            had = True
            snippet = snippet.replace(m, enc)
    return snippet, had


def js_string_escape(s: str) -> str:
    """Строка для одинарных кавычек JS внутри HTML-атрибута: \\xNN не содержат
    ни кавычек, ни &, ни угловых скобок — безопасно в обоих контекстах."""
    return (str(s).replace("\\", "\\\\").replace("'", "\\x27").replace('"', "\\x22")
            .replace("<", "\\x3c").replace(">", "\\x3e").replace("&", "\\x26")
            .replace("\n", "\\n").replace("\r", "\\r"))


class _VastAd:
    __slots__ = ("name", "open_end", "close_start", "last_imp_end", "has_erid", "linears", "has_creative")

    def __init__(self, name: str, open_end: int):
        self.name = name
        self.open_end = open_end
        self.close_start = 0
        self.last_imp_end = 0
        self.has_erid = False
        self.linears: list[dict] = []
        self.has_creative = False


def _tag_end(data: bytes, start: int) -> int:
    """Смещение сразу после '>' тега, начинающегося на start (кавычки учтены)."""
    i, n, q = start, len(data), None
    while i < n:
        ch = data[i:i + 1]
        if q:
            if ch == q:
                q = None
        elif ch in (b'"', b"'"):
            q = ch
        elif ch == b">":
            return i + 1
        i += 1
    raise ValueError("vast: unterminated tag")


def _scan_vast(vast: str) -> list[_VastAd]:
    """Один проход expat: well-formedness + байтовые смещения InLine/Wrapper,
    Impression, Linear, TrackingEvents, VideoClicks, Erid. ValueError — не XML.
    Смещения — в байтах UTF-8 исходника (inject работает по bytes)."""
    data = vast.encode("utf-8")
    parser = expat.ParserCreate()
    # stack: (name, tag_start, tag_end, self_closing) — expat в EndElementHandler
    # даёт индекс начала </X>, а для <X/> — индекс сразу после '/>'.
    state = {"stack": [], "ads": [], "cur": None, "cur_lin": None, "ad_depth": 0, "lin_depth": 0, "root": None}

    def _local(name: str) -> str:
        return name.rsplit(":", 1)[-1]

    def start(name, attrs):
        name = _local(name)
        st = state["stack"]
        if state["root"] is None:
            if name != "VAST":
                raise ValueError("vast: root element is not <VAST>")
            state["root"] = name
        tag_start = parser.CurrentByteIndex
        end = _tag_end(data, tag_start)
        st.append((name, tag_start, end, data[end - 2:end] == b"/>"))
        cur, lin = state["cur"], state["cur_lin"]
        if name in ("InLine", "Wrapper") and cur is None:
            ad = _VastAd(name, end)
            state["ads"].append(ad)
            state["cur"], state["ad_depth"] = ad, len(st)
        elif cur is not None and name == "Creatives" and len(st) == state["ad_depth"] + 1:
            cur.has_creative = True
        elif cur is not None and name == "Linear":
            ln = {"open_end": end, "tracking_open_end": 0, "clicks_open_end": 0}
            cur.linears.append(ln)
            state["cur_lin"], state["lin_depth"] = ln, len(st)
        elif lin is not None and name == "TrackingEvents" and len(st) == state["lin_depth"] + 1:
            lin["tracking_open_end"] = end
        elif lin is not None and name == "VideoClicks" and len(st) == state["lin_depth"] + 1:
            lin["clicks_open_end"] = end
        elif cur is not None and name == "Erid":
            cur.has_erid = True

    def end_(name):
        name = _local(name)
        st = state["stack"]
        depth = len(st)
        _, tag_start, tag_end, self_closing = st.pop()
        idx = parser.CurrentByteIndex
        cur = state["cur"]
        if self_closing:
            close_start, end = tag_start, tag_end
        else:
            close_start, end = idx, _tag_end(data, idx)
        if cur is not None and depth == state["ad_depth"] + 1 and name == "Impression":
            cur.last_imp_end = end
        if state["cur_lin"] is not None and depth == state["lin_depth"]:
            state["cur_lin"] = None
        if cur is not None and depth == state["ad_depth"]:
            if self_closing:
                # <InLine/> — пустой контейнер, вставлять некуда; не считаем его Ad.
                state["ads"].remove(cur)
            else:
                cur.close_start = close_start
            state["cur"] = None

    parser.StartElementHandler = start
    parser.EndElementHandler = end_
    try:
        parser.Parse(data, True)
    except expat.ExpatError as e:
        raise ValueError(f"vast: {e}") from e
    if state["root"] is None:
        raise ValueError("vast: empty document")
    return state["ads"]


def validate_vast(vast: str) -> None:
    """ValueError, если VAST не разбирается или без <InLine>/<Wrapper>
    (валидатор загрузки креатива; тот же разбор, что у inject_vast_trackers)."""
    if not _scan_vast(vast):
        raise ValueError("vast: no <InLine> or <Wrapper>")


def vast_tracking_nodes(vevent: str) -> str:
    if not vevent:
        return ""
    return "".join(f'<Tracking event="{e}"><![CDATA[{vevent}&e={e}]]></Tracking>' for e in VAST_EVENTS)


def inject_vast_trackers(vast: str, *, impressions=(), vevent: str = "", click_trackings=(), extensions_xml: str = "") -> str:
    """Вставляет наши Impression / TrackingEvents / ClickTracking / Extensions во все
    InLine и Wrapper пользовательского VAST, не переписывая исходную разметку.
    ValueError — не XML или без Ad-контейнера (креатив не торгуется)."""
    ads = _scan_vast(vast)
    if not ads:
        raise ValueError("vast: no <InLine> or <Wrapper>")
    data = vast.encode("utf-8")
    inserts: list[tuple[int, str]] = []
    tracking_xml = vast_tracking_nodes(vevent)
    click_xml = "".join(f"<ClickTracking><![CDATA[{u}]]></ClickTracking>" for u in click_trackings if u)
    imps_xml = "".join(f"<Impression><![CDATA[{u}]]></Impression>" for u in impressions if u)
    for ad in ads:
        if extensions_xml and not ad.has_erid:
            inserts.append((ad.open_end, extensions_xml))
        if imps_xml:
            inserts.append((ad.last_imp_end or ad.open_end, imps_xml))
        if not ad.linears:
            # Wrapper без Creatives: VAST 3 §2.4.1 разрешает Creatives/Linear с
            # TrackingEvents и ClickTracking в обёртке. InLine без Linear — только Impression.
            if ad.name == "Wrapper" and not ad.has_creative and (tracking_xml or click_xml):
                block = "<Creatives><Creative><Linear>"
                if tracking_xml:
                    block += f"<TrackingEvents>{tracking_xml}</TrackingEvents>"
                if click_xml:
                    block += f"<VideoClicks>{click_xml}</VideoClicks>"
                block += "</Linear></Creative></Creatives>"
                inserts.append((ad.close_start, block))
            continue
        for ln in ad.linears:
            if tracking_xml:
                if ln["tracking_open_end"]:
                    inserts.append((ln["tracking_open_end"], tracking_xml))
                else:
                    inserts.append((ln["open_end"], f"<TrackingEvents>{tracking_xml}</TrackingEvents>"))
            if click_xml:
                if ln["clicks_open_end"]:
                    inserts.append((ln["clicks_open_end"], click_xml))
                else:
                    inserts.append((ln["open_end"], f"<VideoClicks>{click_xml}</VideoClicks>"))
    inserts.sort(key=lambda x: x[0])  # стабильно: порядок в одной точке сохраняется
    out, prev = [], 0
    for at, text in inserts:
        out.append(data[prev:at])
        out.append(text.encode("utf-8"))
        prev = at
    out.append(data[prev:])
    return b"".join(out).decode("utf-8")
