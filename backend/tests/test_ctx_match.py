"""Категории площадки (IAB): Python-эталон против общих векторов с Go-биддером.

Тот же файл читает bidder-go/internal/rtb/match_vectors_test.go: расхождение
эталона и биддера валит оба набора тестов (docs/CONTRACTS.md, раздел 1.1).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import ctx_match
from routers.rtb import _match_targeting
from targeting_contract import validate_targeting

VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "bidder-go" / "internal" / "targeting" / "testdata" / "match_cases.json")
    .read_text(encoding="utf-8")
)


@pytest.mark.parametrize("raw, want", VECTORS["iab_normalize"])
def test_normalize_iab_code(raw, want):
    assert ctx_match.normalize_iab_code(raw) == want


@pytest.mark.parametrize("want_code, have, expected", VECTORS["iab_match"])
def test_iab_match(want_code, have, expected):
    assert ctx_match.iab_match(want_code, have) is expected


@pytest.mark.parametrize("case", VECTORS["request_cats"], ids=lambda c: c["name"])
def test_request_cats(case):
    assert ctx_match.request_cats(case["req"]) == case["want"]


@pytest.mark.parametrize("case", VECTORS["match"], ids=lambda c: c["name"])
def test_match_targeting_vectors(case):
    # Тот же путь, что у кампании: сначала валидация контрактом, потом матчинг.
    targeting = validate_targeting(case["targeting"])
    assert _match_targeting(targeting, case["req"]) is case["want"]


def test_vectors_are_not_trivially_small():
    assert len(VECTORS["match"]) >= 20 and len(VECTORS["iab_match"]) >= 15 and len(VECTORS["request_cats"]) >= 10


def test_match_targeting_without_iab_keys_ignores_categories():
    # Регрессия: кампании без IAB-ключей не зависят от категорий запроса.
    for site in ({}, {"cat": ["IAB9"]}, {"cat": []}):
        assert _match_targeting({}, {"site": site}) is True


def test_nil_like_targeting_still_passes():
    assert _match_targeting({}, {}) is True
    assert _match_targeting({"iab_categories": [], "iab_block": []}, {"site": {"cat": ["IAB1"]}}) is True


def test_neighbours_domain_and_keywords_unchanged():
    req = {"site": {"domain": "news.example.ru", "cat": ["IAB1"], "keywords": "ипотека", "page": "https://news.example.ru/x"}}
    assert _match_targeting({"domain_allowlist": ["news.example.ru"], "iab_categories": ["IAB1"]}, req) is True
    assert _match_targeting({"domain_blocklist": ["news.example.ru"]}, req) is False
    assert _match_targeting({"keywords_allow": ["ипотека"]}, req) is True
    assert _match_targeting({"keywords_allow": ["авто"]}, req) is False


def test_long_and_garbage_values_do_not_raise():
    long_code = "A" * 100_000
    assert ctx_match.iab_match(long_code, "IAB1") is False
    assert ctx_match.iab_match(long_code, long_code) is True
    for value in ("\x00", "IAB\u0000", "  \u00a0 ", "ІАВ1", None, 5, ["IAB1"], {"a": 1}):
        ctx_match.normalize_iab_code(value)
        assert ctx_match.iab_match("IAB1", value) is False


def test_collect_cats_large_input_is_fast_and_deduplicated():
    codes = [f"IAB{'1' * (i % 50 + 1)}" for i in range(10_000)] + [f"iab{'1' * (i % 50 + 1)}" for i in range(10_000)]
    started = time.perf_counter()
    got = ctx_match.collect_cats(codes)
    assert len(got) == 50
    assert time.perf_counter() - started < 1.0


def test_request_cats_tolerates_wrong_container_types():
    assert ctx_match.request_cats({"site": "not-an-object", "app": None}) == []
    assert ctx_match.request_cats({"site": {"content": "x", "publisher": []}}) == []
    assert ctx_match.request_cats({"site": {"cat": "IAB1"}}) == ["IAB1"]
