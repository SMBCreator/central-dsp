"""Макросы ${CDSP_<ИМЯ>} в ссылке перехода и пикселях креатива.

Единый каталог — backend/url_macros.json (его же читает фронтенд-тест паритета,
а Go сверяет свою таблицу в bidder-go/internal/macros: TestCatalogParity).
Подстановка идёт на бидере в момент ставки; этот модуль — эталон для общих
векторов (bidder-go/internal/macros/testdata/macro_cases.json), валидация на
сохранении креатива и сборка контекста для Python-пути ставки.

Правила те же, что в Go: значения URL-кодируются (пробел — %20), нет значения —
пустая строка, макрос не из каталога остаётся как есть, подставленное значение
повторно не разворачивается. Макросы SSP (${AUCTION_PRICE} и др.) и
${CLICK_URL...} не наши и не проверяются.
"""
from __future__ import annotations

import difflib
import json
import random
import re
import time
from pathlib import Path
from urllib.parse import quote

from country_codes import normalize_country

MACRO_PREFIX = "${CDSP_"

CATALOG: list[dict] = json.loads(Path(__file__).with_name("url_macros.json").read_text(encoding="utf-8"))
KEYS: tuple[str, ...] = tuple(e["key"] for e in CATALOG)
_KEYSET = frozenset(KEYS)

_KNOWN_RE = re.compile(r"\$\{CDSP_([A-Z0-9_]+)\}")
# Любая попытка написать наш макрос, в том числе в другом регистре (опечатка).
_ANY_RE = re.compile(r"\$\{(?i:CDSP_)[^}]*\}")


def find_macros(url: str | None) -> list[str]:
    """Ключи известных макросов каталога в порядке появления, без повторов."""
    if not url:
        return []
    seen: dict[str, None] = {}
    for m in _KNOWN_RE.finditer(url):
        if m.group(1) in _KEYSET:
            seen.setdefault(m.group(1), None)
    return list(seen)


def has_macros(*urls: str | None) -> bool:
    return any(find_macros(u) for u in urls)


def unknown_macros(url: str | None) -> list[str]:
    """Токены вида ${CDSP_…}, которых нет в каталоге (опечатка, другой регистр)."""
    if not url:
        return []
    bad: list[str] = []
    for tok in _ANY_RE.findall(url):
        name = tok[len(MACRO_PREFIX):-1]
        if not tok.startswith(MACRO_PREFIX) or name not in _KEYSET:
            if tok not in bad:
                bad.append(tok)
    return bad


def suggest(token: str) -> list[str]:
    """Ближайшие по написанию макросы каталога для опечатки (до трёх)."""
    name = token[len(MACRO_PREFIX):-1].upper() if token.endswith("}") else token.upper()
    return ["${CDSP_" + k + "}" for k in difflib.get_close_matches(name, KEYS, n=3, cutoff=0.6)]


def validate_macros(url: str | None) -> str | None:
    """Текст ошибки для неизвестных макросов или None. Для save-валидации."""
    bad = unknown_macros(url)
    if not bad:
        return None
    parts = []
    for tok in bad:
        hint = suggest(tok)
        parts.append(f"Неизвестный макрос {tok}" + (f". Возможно: {', '.join(hint)}" if hint else ""))
    return "; ".join(parts)


def expand(template: str, ctx: dict[str, str]) -> str:
    """Подстановка значений контекста (ключи без префикса: «COUNTRY»)."""
    if MACRO_PREFIX not in template:
        return template

    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in _KEYSET:
            return m.group(0)
        v = ctx.get(key)
        return quote(str(v), safe="", errors="replace") if v else ""

    return _KNOWN_RE.sub(repl, template)


_CONNECTION = {1: "ethernet", 2: "wifi", 3: "cellular", 4: "cellular", 5: "cellular", 6: "cellular", 7: "cellular"}


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def build_context(
    *, req: dict, imp: dict, creative: dict, partner_id, bid_id: str,
    device_type: str, os_name: str, browser: str, now: float | None = None,
    cachebuster: int | None = None,
) -> dict[str, str]:
    """Значения каталога для одной ставки; зеркало bidder-go rtb.buildMacroContext.
    IP, идентификаторы устройства, координаты, название SSP и цена клиринга
    не входят в каталог и сюда не попадают."""
    device = req.get("device") or {}
    geo = device.get("geo") or {}
    site = req.get("site") or req.get("app") or {}
    app = req.get("app") or {}
    publisher = site.get("publisher") or {}
    # Как view.Domain в Go: у site — site.domain; только у app — app.domain, иначе bundle.
    if req.get("site"):
        domain = (site.get("domain") or "").lower()
    else:
        domain = (app.get("domain") or app.get("bundle") or "").lower()
    site_id = _s((req.get("site") or {}).get("id")) or _s(app.get("id"))
    w, h = creative.get("width") or 0, creative.get("height") or 0
    try:
        conn = _CONNECTION.get(int(device.get("connectiontype") or 0), "unknown")
    except (TypeError, ValueError):
        conn = "unknown"
    ts = int(now if now is not None else time.time())
    ctx = {
        "BID_ID": bid_id,
        "AUCTION_ID": _s(req.get("id")),
        "IMP_ID": _s(imp.get("id")),
        "CAMPAIGN_ID": _s(creative.get("campaign_id")),
        "GROUP_ID": _s(creative.get("li_id")),
        "CREATIVE_ID": _s(creative.get("creative_id")),
        "SITE_ID": site_id,
        "PUBLISHER_ID": _s(publisher.get("id")),
        "PLACEMENT_ID": _s(imp.get("tagid")),
        "DOMAIN": domain,
        "BUNDLE": _s(app.get("bundle")).lower(),
        "SSP_ID": _s(partner_id),
        "COUNTRY": normalize_country(geo.get("country")),
        "REGION": _s(geo.get("region")),
        "CITY": _s(geo.get("city")),
        "DEVICE_TYPE": device_type,
        "OS": os_name,
        "OS_VERSION": _s(device.get("osv")),
        "BROWSER": browser,
        "DEVICE_MAKE": _s(device.get("make")).lower(),
        "DEVICE_MODEL": _s(device.get("model")),
        "CARRIER": _s(device.get("carrier")),
        "CONNECTION": conn,
        "LANGUAGE": _s(device.get("language")).lower(),
        "AD_FORMAT": _s(creative.get("type")),
        "SIZE": f"{w}x{h}" if w > 0 and h > 0 else "",
        "WIDTH": str(w) if w > 0 else "",
        "HEIGHT": str(h) if h > 0 else "",
        "TIMESTAMP": str(ts),
        "CACHEBUSTER": str(cachebuster if cachebuster is not None else random.randrange(100_000_000)),
    }
    return ctx
