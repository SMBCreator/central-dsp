"""Справочник допустимых размеров баннеров — единый источник правды.

Копия для UI: frontend/lib/ad-sizes-catalog.ts (резервный список хука useAdSizes).
Паритет закреплён тестом tests/test_ad_sizes.py::TestAdSizesParity — при правке
списка менять обе копии.

max_bytes — необязательный индивидуальный потолок веса для размера (рекомендация
площадок/SSP). None = действует только общий лимит MAX_IMAGE_UPLOAD_BYTES.
"""
import math
import os

AD_SIZES: list[dict] = [
    {"size": "120x600", "width": 120, "height": 600, "label": "Skyscraper", "category": "desktop", "max_bytes": None},
    {"size": "160x600", "width": 160, "height": 600, "label": "Wide Skyscraper", "category": "desktop", "max_bytes": None},
    {"size": "200x200", "width": 200, "height": 200, "label": "Small Square", "category": "universal", "max_bytes": None},
    {"size": "240x400", "width": 240, "height": 400, "label": "Vertical Rectangle", "category": "desktop", "max_bytes": None},
    {"size": "240x600", "width": 240, "height": 600, "label": "Vertical Banner", "category": "desktop", "max_bytes": None},
    {"size": "250x250", "width": 250, "height": 250, "label": "Square", "category": "universal", "max_bytes": None},
    {"size": "300x50", "width": 300, "height": 50, "label": "Mobile Banner", "category": "mobile", "max_bytes": None},
    {"size": "300x100", "width": 300, "height": 100, "label": "Mobile Banner Large", "category": "mobile", "max_bytes": None},
    {"size": "300x250", "width": 300, "height": 250, "label": "Medium Rectangle", "category": "universal", "max_bytes": None},
    {"size": "300x300", "width": 300, "height": 300, "label": "Square 300", "category": "universal", "max_bytes": None},
    {"size": "300x500", "width": 300, "height": 500, "label": "Tall Rectangle", "category": "universal", "max_bytes": None},
    {"size": "300x600", "width": 300, "height": 600, "label": "Half Page", "category": "desktop", "max_bytes": None},
    {"size": "320x50", "width": 320, "height": 50, "label": "Mobile Leaderboard", "category": "mobile", "max_bytes": None},
    {"size": "320x100", "width": 320, "height": 100, "label": "Large Mobile Banner", "category": "mobile", "max_bytes": None},
    {"size": "320x480", "width": 320, "height": 480, "label": "Mobile Interstitial", "category": "mobile", "max_bytes": None},
    {"size": "336x280", "width": 336, "height": 280, "label": "Large Rectangle", "category": "desktop", "max_bytes": None},
    {"size": "360x640", "width": 360, "height": 640, "label": "Mobile Fullscreen", "category": "mobile", "max_bytes": None},
    {"size": "468x60", "width": 468, "height": 60, "label": "Full Banner", "category": "desktop", "max_bytes": None},
    {"size": "480x320", "width": 480, "height": 320, "label": "Mobile Interstitial Landscape", "category": "mobile", "max_bytes": None},
    {"size": "500x200", "width": 500, "height": 200, "label": "Wide Rectangle", "category": "desktop", "max_bytes": None},
    {"size": "580x400", "width": 580, "height": 400, "label": "Netboard", "category": "desktop", "max_bytes": None},
    {"size": "728x90", "width": 728, "height": 90, "label": "Leaderboard", "category": "desktop", "max_bytes": None},
    {"size": "970x90", "width": 970, "height": 90, "label": "Large Leaderboard", "category": "desktop", "max_bytes": None},
    {"size": "970x250", "width": 970, "height": 250, "label": "Billboard", "category": "desktop", "max_bytes": None},
]

ALLOWED_SIZES: frozenset[tuple[int, int]] = frozenset((s["width"], s["height"]) for s in AD_SIZES)


def size_key(width: int, height: int) -> str:
    return f"{width}x{height}"


def is_allowed(width: int | None, height: int | None) -> bool:
    return width is not None and height is not None and (width, height) in ALLOWED_SIZES


def nearest_sizes(width: int, height: int, limit: int = 3) -> list[str]:
    """До `limit` ближайших допустимых размеров: близость соотношения сторон и площади
    (в логарифмах, чтобы 2× больше и 2× меньше весили одинаково). Та же формула,
    что matchSize во frontend/lib/creative-batch.ts."""
    if width <= 0 or height <= 0:
        return []

    def score(s: dict) -> float:
        ratio = abs(math.log((width / height) / (s["width"] / s["height"])))
        area = abs(math.log((width * height) / (s["width"] * s["height"])))
        return ratio * 2 + area

    return [s["size"] for s in sorted(AD_SIZES, key=score)[:limit]]


def size_strict() -> bool:
    """CREATIVE_SIZE_STRICT=0 — аварийно отключить серверную проверку размера баннера."""
    return os.environ.get("CREATIVE_SIZE_STRICT", "1").strip() != "0"
