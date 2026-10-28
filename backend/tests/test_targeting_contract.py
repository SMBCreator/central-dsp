"""Контракт ключей таргетинга через слои: TS-интерфейс → Pydantic → Go-структура.

Источник правды — `targeting_contract.TARGETING_KEYS`. Тест парсит исходники
соседних слоёв (не импортирует их): дрейф любого слоя — добавили ключ в UI
и забыли в Go, переименовали поле в Go — валит CI с точным именем ключа.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas import LineItemIn, LineItemPatch
from targeting_contract import (
    BOOL,
    DEPRECATED_KEYS,
    INT,
    INT_LIST,
    STR,
    STR_LIST,
    TARGETING_KEYS,
    validate_targeting,
)

REPO = Path(__file__).resolve().parents[2]
GO_FEED = REPO / "bidder-go" / "internal" / "feed" / "feed.go"
GO_HANDLER = REPO / "bidder-go" / "internal" / "rtb" / "handler.go"
TS_TYPES = REPO / "frontend" / "lib" / "types.ts"
TS_EDITOR = REPO / "frontend" / "components" / "targeting-editor.tsx"

_GO_TYPE_MAP = {
    "[]string": STR_LIST,
    "[]int": INT_LIST,
    "int": INT,
    "string": STR,
    "bool": BOOL,
}
_TS_TYPE_MAP = {
    "string[]": STR_LIST,
    "number[]": INT_LIST,
    "number": INT,
    "string": STR,
    "boolean": BOOL,
}


def _go_struct_fields(src: str, name: str) -> dict[str, str]:
    m = re.search(rf"type {name} struct \{{(.*?)\n\}}", src, re.DOTALL)
    assert m, f"Go struct {name} not found"
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        fm = re.match(r"\s*([A-Za-z]+)\s+(\S+)\s+`json:\"([a-z_]+)", line)
        if fm:
            fields[fm.group(3)] = _GO_TYPE_MAP[fm.group(2)]
    return fields


def _ts_interface_fields(src: str, name: str) -> dict[str, str]:
    m = re.search(rf"export interface {name} \{{(.*?)\n\}}", src, re.DOTALL)
    assert m, f"TS interface {name} not found"
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        fm = re.match(r"\s*([a-z_]+)\??:\s*([a-z\[\]]+)", line)
        if fm:
            fields[fm.group(1)] = _TS_TYPE_MAP[fm.group(2)]
    return fields


# --- слои совпадают с контрактом --------------------------------------------


def test_go_feed_targeting_matches_contract():
    go = _go_struct_fields(GO_FEED.read_text(encoding="utf-8"), "Targeting")
    assert set(go) == set(TARGETING_KEYS), {
        "only_in_go": sorted(set(go) - set(TARGETING_KEYS)),
        "only_in_contract": sorted(set(TARGETING_KEYS) - set(go)),
    }
    mismatched = {k: (go[k], TARGETING_KEYS[k]) for k in go if go[k] != TARGETING_KEYS[k]}
    assert not mismatched, f"type drift (go, contract): {mismatched}"


def test_ts_targeting_interface_matches_contract():
    ts = _ts_interface_fields(TS_TYPES.read_text(encoding="utf-8"), "Targeting")
    assert set(ts) == set(TARGETING_KEYS), {
        "only_in_ts": sorted(set(ts) - set(TARGETING_KEYS)),
        "only_in_contract": sorted(set(TARGETING_KEYS) - set(ts)),
    }
    mismatched = {k: (ts[k], TARGETING_KEYS[k]) for k in ts if ts[k] != TARGETING_KEYS[k]}
    assert not mismatched, f"type drift (ts, contract): {mismatched}"


def test_every_contract_key_is_consumed_by_go_matcher():
    """Ключ в структуре, но не в feedTargetingToMatch/handler — «принимается,
    но не работает». Ищем Go-имя поля в handler.go."""
    go_src = GO_FEED.read_text(encoding="utf-8")
    m = re.search(r"type Targeting struct \{(.*?)\n\}", go_src, re.DOTALL)
    assert m
    go_names = dict(re.findall(r"\s*([A-Za-z]+)\s+\S+\s+`json:\"([a-z_]+)", m.group(1)))
    # GEO v2-ключи превращаются в множества при рефреше (feed.go, geo.NewSets),
    # а не в handler.go: hot path читает уже готовый Candidate.Geo.
    handler = GO_HANDLER.read_text(encoding="utf-8") + go_src[m.end():]
    unused = [json for go_name, json in go_names.items() if not re.search(rf"\.{go_name}\b", handler)]
    assert not unused, f"targeting keys parsed by Go but never read in handler.go: {unused}"


def test_every_contract_key_is_editable_in_ui():
    """Ключ есть в контракте, но UI не даёт его задать — фича без ручки."""
    editor = TS_EDITOR.read_text(encoding="utf-8")
    missing = [k for k in TARGETING_KEYS if not re.search(rf"\b{k}\b", editor)]
    assert not missing, f"targeting keys without a control in targeting-editor.tsx: {missing}"


def test_deprecated_keys_are_not_in_ts_or_go():
    ts = TS_TYPES.read_text(encoding="utf-8")
    go = GO_FEED.read_text(encoding="utf-8")
    ts_iface = re.search(r"export interface Targeting \{(.*?)\n\}", ts, re.DOTALL).group(1)
    for k in DEPRECATED_KEYS:
        assert not re.search(rf"\b{k}\??:", ts_iface), f"{k} still declared in TS Targeting"
        assert f'json:"{k}"' not in go, f"{k} still declared in Go Targeting"


# --- Pydantic отвергает то, что Go не понимает --------------------------------


def _li(**targeting):
    return LineItemIn(campaign_id="c", name="n", bid_price=1, targeting=targeting)


def test_valid_targeting_passes_and_drops_nulls():
    li = _li(geo_countries=["RU"], hours=[0, 23], age_min=18, secure_only=True, os=None)
    assert li.targeting == {"geo_countries": ["RU"], "hours": [0, 23], "age_min": 18, "secure_only": True}


@pytest.mark.parametrize(
    "bad, fragment",
    [
        ({"geo_countrys": ["RU"]}, "неизвестный ключ"),
        ({"ad_types": ["banner"]}, "не поддерживается"),
        ({"geo_countries": "RU"}, "список строк"),
        ({"geo_countries": [1, 2]}, "список строк"),
        ({"hours": ["9"]}, "список целых"),
        ({"hours": [24]}, "0..23"),
        ({"days": [7]}, "0..6"),
        ({"age_min": "18"}, "целое число"),
        ({"age_min": True}, "целое число"),
        ({"age_min": 30, "age_max": 20}, "age_min не может быть больше"),
        ({"viewability_min": 101}, "0..100"),
        ({"secure_only": "yes"}, "true/false"),
        ({"secure_only": 1}, "true/false"),
        ({"os_version_min": 14}, "ожидается строка"),
        ({"video_placements": [0]}, "1..5"),
    ],
)
def test_invalid_targeting_is_rejected_with_reason(bad, fragment):
    with pytest.raises(ValidationError) as ei:
        _li(**bad)
    assert fragment in str(ei.value)


def test_patch_validates_too_but_allows_none():
    assert LineItemPatch(targeting=None).targeting is None
    with pytest.raises(ValidationError):
        LineItemPatch(targeting={"nope": 1})


def test_list_length_cap():
    with pytest.raises(ValueError):
        validate_targeting({"domain_blocklist": ["x"] * 5001})
