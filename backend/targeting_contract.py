"""Контракт ключей таргетинга line_items.targeting между слоями.

Единственный источник правды по именам и типам ключей, которые понимает
Go-биддер (`bidder-go/internal/feed/feed.go: type Targeting`). Через него:

- Pydantic (`schemas.LineItemIn/Patch`) отвергает неизвестные ключи и
  неверные типы на входе — опечатка в UI больше не «молча не работает»;
- `tests/test_targeting_contract.py` парсит Go-структуру и TS-интерфейс и
  падает, если любой слой уехал от этого списка.

Тип — то, что Go декодирует без ошибки. Пустое значение (пустой список, 0,
false) для Go означает «нет ограничения», поэтому здесь оно тоже допустимо.
"""

from __future__ import annotations

from typing import Any

STR_LIST = "list[str]"
INT_LIST = "list[int]"
INT = "int"
STR = "str"
BOOL = "bool"

TARGETING_KEYS: dict[str, str] = {
    # География / устройство / среда
    "geo_countries": STR_LIST,
    "geo_regions": STR_LIST,
    "geo_cities": STR_LIST,
    "device_types": STR_LIST,
    "device_makes": STR_LIST,
    "os": STR_LIST,
    "os_version_min": STR,
    "browsers": STR_LIST,
    "languages": STR_LIST,
    "connection_types": STR_LIST,
    "carriers": STR_LIST,
    "traffic_types": STR_LIST,
    "ip_ranges": STR_LIST,
    # Контент / инвентарь
    "iab_categories": STR_LIST,
    "iab_block": STR_LIST,
    "domain_allowlist": STR_LIST,
    "domain_blocklist": STR_LIST,
    "app_bundles_allow": STR_LIST,
    "app_bundles_block": STR_LIST,
    "publisher_allow": STR_LIST,
    "publisher_block": STR_LIST,
    "keywords_allow": STR_LIST,
    "keywords_block": STR_LIST,
    # Аудитория / демография / расписание
    "age_min": INT,
    "age_max": INT,
    "genders": STR_LIST,
    "hours": INT_LIST,
    "days": INT_LIST,
    "audience_include": STR_LIST,
    "audience_exclude": STR_LIST,
    # Поставка
    "ssp_allowlist": STR_LIST,
    "deal_ids": STR_LIST,
    "schain_required": BOOL,
    # Слот
    "banner_positions": INT_LIST,
    "interstitial_only": BOOL,
    "exclude_interstitial": BOOL,
    "rewarded_only": BOOL,
    "secure_only": BOOL,
    "video_min_duration": INT,
    "video_skippable_only": BOOL,
    "video_placements": INT_LIST,
    "video_protocols": INT_LIST,
    "viewability_min": INT,
    # Регуляторика
    "block_coppa": BOOL,
    "block_gdpr": BOOL,
    "skip_dnt": BOOL,
}

# Ключи, которые UI/старые данные могли содержать, но Go никогда не читал.
# Отвергаем явно с понятным сообщением, а не общим «неизвестный ключ».
DEPRECATED_KEYS: dict[str, str] = {
    "ad_types": "формат объявления задаётся креативом, а не таргетингом группы",
}

_RANGES: dict[str, tuple[int, int]] = {
    "age_min": (0, 120),
    "age_max": (0, 120),
    "video_min_duration": (0, 3600),
    "viewability_min": (0, 100),
}
_ELEMENT_RANGES: dict[str, tuple[int, int]] = {
    "hours": (0, 23),
    "days": (0, 6),
    "banner_positions": (0, 7),
    "video_placements": (1, 5),
    "video_protocols": (1, 14),
}
MAX_LIST_LEN = 5000


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_targeting(t: dict[str, Any]) -> dict[str, Any]:
    """Валидирует словарь таргетинга по контракту. Возвращает нормализованный
    словарь (None-значения удалены). Бросает ValueError с человекочитаемым
    текстом — Pydantic превратит его в 422."""
    if not isinstance(t, dict):
        raise TypeError("targeting должен быть объектом")
    out: dict[str, Any] = {}
    for key, value in t.items():
        if key in DEPRECATED_KEYS:
            raise ValueError(f"targeting.{key}: ключ не поддерживается — {DEPRECATED_KEYS[key]}")
        kind = TARGETING_KEYS.get(key)
        if kind is None:
            raise ValueError(f"targeting.{key}: неизвестный ключ таргетинга")
        if value is None:
            continue
        if kind == STR_LIST:
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError(f"targeting.{key}: ожидается список строк")
            if len(value) > MAX_LIST_LEN:
                raise ValueError(f"targeting.{key}: не более {MAX_LIST_LEN} значений")
        elif kind == INT_LIST:
            if not isinstance(value, list) or not all(_is_int(x) for x in value):
                raise ValueError(f"targeting.{key}: ожидается список целых чисел")
            lo, hi = _ELEMENT_RANGES.get(key, (-(2**31), 2**31 - 1))
            if any(x < lo or x > hi for x in value):
                raise ValueError(f"targeting.{key}: значения должны быть в диапазоне {lo}..{hi}")
        elif kind == INT:
            if not _is_int(value):
                raise ValueError(f"targeting.{key}: ожидается целое число")
            lo, hi = _RANGES.get(key, (0, 2**31 - 1))
            if value < lo or value > hi:
                raise ValueError(f"targeting.{key}: значение должно быть в диапазоне {lo}..{hi}")
        elif kind == STR:
            if not isinstance(value, str):
                raise ValueError(f"targeting.{key}: ожидается строка")
        elif kind == BOOL:
            if not isinstance(value, bool):
                raise ValueError(f"targeting.{key}: ожидается true/false")
        out[key] = value
    if "age_min" in out and "age_max" in out and out["age_min"] > out["age_max"]:
        raise ValueError("targeting.age_min не может быть больше age_max")
    return out
