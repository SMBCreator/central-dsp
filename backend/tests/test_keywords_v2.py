"""Слова v2, режим «И / ИЛИ» и «без тематики»: Python-эталон на общих векторах с Go.

Те же файлы читает bidder-go (internal/targeting/keywords_test.go и
internal/rtb/context_vectors_test.go): расхождение эталона и биддера валит
оба набора тестов (docs/CONTRACTS.md, разделы 1.3–1.5).
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from pydantic import ValidationError

import ctx_match
from routers.rtb import _match_targeting
from schemas import LineItemIn, LineItemPatch
from targeting_contract import validate_targeting

TESTDATA = Path(__file__).resolve().parents[2] / "bidder-go" / "internal" / "targeting" / "testdata"
KEYWORD_CASES = json.loads((TESTDATA / "keyword_cases.json").read_text(encoding="utf-8"))["cases"]
CONTEXT_CASES = json.loads((TESTDATA / "context_cases.json").read_text(encoding="utf-8"))["match"]


# --- общие векторы слов -----------------------------------------------------


@pytest.mark.parametrize("case", KEYWORD_CASES, ids=[c["name"] for c in KEYWORD_CASES])
def test_keyword_vectors(case):
    allow_ok, blocked = ctx_match.eval_keywords(case["mode"], case["haystack"], case["allow"], case["block"])
    assert (allow_ok, blocked) == (case["expect_allow"], case["expect_block"])


def test_keyword_vectors_are_not_trivial():
    assert len(KEYWORD_CASES) >= 100
    assert {c["mode"] for c in KEYWORD_CASES} == {"", "substring", "word_start", "whole_word"}


# --- нормализация и разбиение ----------------------------------------------


@pytest.mark.parametrize(
    "text, want",
    [
        ("iPhone-15 Pro", ["iphone", "15", "pro"]),
        ("Ёлка, ёж", ["елка", "еж"]),
        ("под_черту/слэш.точка%процент@собака", ["под", "черту", "слэш", "точка", "процент", "собака"]),
        ("!!!", []),
        ("", []),
        ("٣٤", ["٣٤"]),
    ],
)
def test_tokenize(text, want):
    assert ctx_match.tokenize(ctx_match.norm_text(text)) == want


@pytest.mark.parametrize(
    "raw, want",
    [
        ("https://x.ru/%D0%B8%D0%BF", "https://x.ru/ип"),
        ("/a+b", "/a+b"),
        ("/%ZZ%D0%B8", "/%zz%d0%b8"),
        ("/%D0%98%D0%9F", "/ип"),
        ("/%D0", "/\ufffd"),
        ("100%", "100%"),
    ],
)
def test_norm_page(raw, want):
    assert ctx_match.norm_page(raw) == want


def test_limits():
    long_word = "а" * (ctx_match.KW_MAX_WORD_CHARS + 1)
    nine = "a " * (ctx_match.KW_MAX_PHRASE_TOKENS + 1)
    exact = "б" * ctx_match.KW_MAX_WORD_CHARS
    eight = ("a " * ctx_match.KW_MAX_PHRASE_TOKENS).strip()
    keys = ctx_match.prepare_kw_keys([long_word, nine, "!!!", "", exact, eight, "ok"])
    assert [k[0] for k in keys] == [exact, " ".join(eight.split()), "ok"]

    many = [f"w{'x' * (i % 5)}{chr(ord('a') + i % 26)}" for i in range(ctx_match.KW_MAX_WORDS + 50)]
    assert len(ctx_match.prepare_kw_keys(many)) == ctx_match.KW_MAX_WORDS

    body = "я" * ctx_match.KW_MAX_SOURCE_CHARS + " хвост"
    assert ctx_match.eval_keywords("substring", ["", "", "", body], ["хвост"], [])[0] is False
    edge = "я" * (ctx_match.KW_MAX_SOURCE_CHARS - 6) + " хвост"
    assert ctx_match.eval_keywords("substring", ["", "", "", edge], ["хвост"], [])[0] is True

    def tokens(a: int, b: int) -> str:
        return "".join(f"t{i:03d} " for i in range(a, b))

    src = ["", tokens(0, 300), "", tokens(300, 600)]
    _, toks, _ = ctx_match.build_kw_text(src)
    assert len([t for t in toks if t != "\x00"]) == ctx_match.KW_MAX_TOKENS
    assert ctx_match.eval_keywords("whole_word", src, ["t511"], [])[0] is True
    assert ctx_match.eval_keywords("whole_word", src, ["t512"], [])[0] is False


def test_no_exception_on_any_input():
    samples = [
        "", "%", "%%", "%2", "%zz", "%C0%AF", "%ED%A0%80", "a\x00b", "\x00", "ёЁ", "İ", "ΑΣ", "\u0301", "%00",
        "%D0" * 5000, "я" * 10000, "𝒜𝒷𝒸", "\u200b\u200d", "\udcff",
    ]
    rng = random.Random(1)
    for _ in range(500):
        samples.append("".join(chr(rng.randrange(0x20, 0x2FFF)) for _ in range(rng.randrange(64))))
    for mode in ("substring", "word_start", "whole_word", ""):
        for s in samples:
            ctx_match.eval_keywords(mode, [s, s, s, s], [s, "a b", "ё"], [s, "%"])


# --- матрица контекста: тематика × слова × режим × опция × стоп-условия -----


@pytest.mark.parametrize("case", CONTEXT_CASES, ids=[c["name"] for c in CONTEXT_CASES])
def test_context_matrix(case):
    assert _match_targeting(case["targeting"], case["req"]) is case["want"]


def test_context_matrix_covers_all_reasons():
    assert len(CONTEXT_CASES) >= 300
    assert {c["reason"] for c in CONTEXT_CASES} == {"", "inventory_iab", "keywords", "context"}


def test_legacy_targeting_unchanged():
    # Кампания без новых ключей: слова ищутся подстрокой в нижнем регистре, ё не заменяется.
    req = {"site": {"content": {"title": "Ёлка"}}}
    assert _match_targeting({"keywords_allow": ["ёлка"]}, req) is True
    assert _match_targeting({"keywords_allow": ["елка"]}, req) is False
    assert _match_targeting({"keywords_match": "word_start", "keywords_allow": ["елка"]}, req) is True


# --- схема и валидация ------------------------------------------------------


def _li(targeting):
    return LineItemIn(campaign_id="c", name="n", bid_price=1, targeting=targeting)


@pytest.mark.parametrize("mode", ["substring", "word_start", "whole_word"])
def test_schema_accepts_valid_keywords_match(mode):
    assert _li({"keywords_match": mode}).targeting["keywords_match"] == mode


@pytest.mark.parametrize("mode", ["any", "", "Word_Start", "prefix", 1, True])
def test_schema_rejects_invalid_keywords_match(mode):
    with pytest.raises(ValidationError):
        _li({"keywords_match": mode})


@pytest.mark.parametrize("mode", ["all", "any"])
def test_schema_accepts_valid_context_mode(mode):
    assert _li({"context_mode": mode}).targeting["context_mode"] == mode


@pytest.mark.parametrize("mode", ["both", "", "ANY", 0, None.__class__])
def test_schema_rejects_invalid_context_mode(mode):
    with pytest.raises(ValidationError):
        _li({"context_mode": mode})


@pytest.mark.parametrize("value", ["true", 1, "yes"])
def test_schema_rejects_non_bool_allow_unknown(value):
    with pytest.raises(ValidationError):
        _li({"iab_allow_unknown": value})


def test_schema_accepts_allow_unknown_bool():
    assert _li({"iab_allow_unknown": True}).targeting["iab_allow_unknown"] is True
    assert _li({"iab_allow_unknown": False}).targeting["iab_allow_unknown"] is False


def test_patch_validates_new_keys_too():
    with pytest.raises(ValidationError):
        LineItemPatch(targeting={"context_mode": "nope"})
    assert LineItemPatch(targeting={"context_mode": "any"}).targeting["context_mode"] == "any"


def test_keyword_limits_enforced_with_keywords_match():
    with pytest.raises(ValidationError, match="не более 200"):
        _li({"keywords_match": "word_start", "keywords_allow": ["a"] * 201})
    with pytest.raises(ValidationError, match="длиннее 100"):
        _li({"keywords_match": "word_start", "keywords_block": ["а" * 101]})
    with pytest.raises(ValidationError, match="не более 8"):
        _li({"keywords_match": "whole_word", "keywords_allow": ["a b c d e f g h i"]})
    ok = _li({"keywords_match": "word_start", "keywords_allow": ["a b c d e f g h", "а" * 100] + ["x"] * 198})
    assert len(ok.targeting["keywords_allow"]) == 200


def test_legacy_lists_not_limited_without_keywords_match():
    # Старые кампании: лимиты слов v2 их не затрагивают.
    big = ["слово"] * 300
    assert len(_li({"keywords_allow": big}).targeting["keywords_allow"]) == 300
    assert validate_targeting({"keywords_allow": ["а" * 150]})["keywords_allow"] == ["а" * 150]


def test_old_campaign_targeting_roundtrips_byte_for_byte():
    old = {"geo_countries": ["KZ"], "iab_categories": ["IAB1"], "keywords_allow": ["ипотека"], "hours": [10]}
    out = validate_targeting(dict(old))
    assert out == old
    assert json.dumps(out, sort_keys=True) == json.dumps(old, sort_keys=True)
    assert not {"iab_allow_unknown", "keywords_match", "context_mode"} & set(out)
