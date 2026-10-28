"""Контекстный матчинг таргетинга: категории площадки (IAB).

Python-эталон для Go-биддера (bidder-go/internal/targeting/iab.go). Правила и
общие тест-векторы — bidder-go/internal/targeting/testdata/match_cases.json:
оба набора тестов читают один файл, расхождение валит CI с обеих сторон.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import unquote_to_bytes

# Символы, которые Go считает пробелами в strings.TrimSpace. Python str.strip()
# по умолчанию режет ещё \x1c-\x1f — для побитового паритета задаём набор явно.
_GO_SPACE = "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"


def normalize_iab_code(code: Any) -> str:
    """Код категории в каноническом виде: без пробелов, в верхнем регистре;
    у кодов IAB «IAB7_3» подчёркивание заменяется дефисом. Коды других
    таксономий остаются как есть (кроме регистра и пробелов)."""
    if not isinstance(code, str):
        return ""
    s = code.strip(_GO_SPACE).upper()
    if s.startswith("IAB") and "_" in s:
        s = s.replace("_", "-")
    return s


def iab_match(want: Any, have: Any) -> bool:
    """Код таргетинга `want` совпал с кодом запроса `have`: коды равны либо
    `want` — tier-1 код без дефиса («IAB7»), а `have` — его подкатегория
    («IAB7-3»). Подкатегория в таргетинге родителя запроса не покрывает."""
    w, h = normalize_iab_code(want), normalize_iab_code(have)
    if not w or not h:
        return False
    if w == h:
        return True
    return "-" not in w and h.startswith(w + "-")


def iab_intersects(wanted: list, have: list) -> bool:
    return any(iab_match(w, h) for w in wanted for h in have)


def collect_cats(*lists: list) -> list[str]:
    """Объединение списков категорий: нормализованные коды без пустых и дублей,
    порядок первого появления."""
    out: list[str] = []
    seen: set[str] = set()
    for lst in lists:
        for raw in lst:
            code = normalize_iab_code(raw)
            if code and code not in seen:
                seen.add(code)
                out.append(code)
    return out


def _cat_list(value: Any) -> list[str]:
    # Как Go CatList: строка или массив строк; всё прочее игнорируется.
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str)]
    return []


def request_cats(req: dict) -> list[str]:
    """Категории площадки запроса: cat, sectioncat, pagecat, content.cat и
    publisher.cat объектов site и app (OpenRTB 2.5). Пусто — тематика не передана."""
    lists: list[list[str]] = []
    for key in ("site", "app"):
        obj = req.get(key)
        if not isinstance(obj, dict):
            continue
        lists.append(_cat_list(obj.get("cat")))
        lists.append(_cat_list(obj.get("sectioncat")))
        lists.append(_cat_list(obj.get("pagecat")))
        content = obj.get("content")
        if isinstance(content, dict):
            lists.append(_cat_list(content.get("cat")))
        publisher = obj.get("publisher")
        if isinstance(publisher, dict):
            lists.append(_cat_list(publisher.get("cat")))
    return collect_cats(*lists)


# --- Слова v2 (ТЗ приложение A) ---------------------------------------------
# Включаются, только если у line item задан keywords_match; без него работает
# легаси-путь без изменений. Go-реализация — bidder-go/internal/targeting/keywords.go;
# общие векторы — bidder-go/internal/targeting/testdata/keyword_cases.json.

KW_MODES = ("substring", "word_start", "whole_word")
KW_MAX_SOURCE_CHARS = 2048
KW_MAX_TOKENS = 512
KW_MAX_WORDS = 200
KW_MAX_WORD_CHARS = 100
KW_MAX_PHRASE_TOKENS = 8
_KW_SEP = "\x00"
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")


def _lower(s: str) -> str:
    """Нижний регистр по символам, как Go unicode.ToLower: без контекста
    (финальная сигма) и без многосимвольных результатов (İ -> i)."""
    out = []
    for ch in s:
        low = ch.lower()
        if len(low) != 1:
            low = "i" if ch == "\u0130" else ch
        out.append(low)
    return "".join(out)


def norm_text(s: str) -> str:
    return _lower(s).replace("ё", "е")


def _unescape(s: str) -> str:
    # Как Go url.PathUnescape: «+» не пробел; любая битая %-последовательность —
    # исходная строка целиком; невалидный UTF-8 после декодирования -> U+FFFD.
    if "%" not in s or _BAD_PERCENT.search(s):
        return s
    return unquote_to_bytes(s).decode("utf-8", "replace")


def norm_page(s: str) -> str:
    return norm_text(_unescape(s))


def _is_word_char(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat[0] in ("L", "M") or cat == "Nd"


def tokenize(s: str) -> list[str]:
    """Слова по Unicode (L*, Nd, M*); всё прочее — разделитель. Не через \\b/\\w:
    в Go \\b только ASCII, результаты разошлись бы."""
    out: list[str] = []
    start = -1
    for i, ch in enumerate(s):
        if _is_word_char(ch):
            if start < 0:
                start = i
            continue
        if start >= 0:
            out.append(s[start:i])
            start = -1
    if start >= 0:
        out.append(s[start:])
    return out


def build_kw_text(sources: list[str]) -> tuple[str, list[str], str]:
    """Текст запроса: N (нормализованная строка), T (токены с разделителем
    \\x00 между источниками и элементами CSV) и tj (T через пробел).
    sources — [site/app.keywords, content.keywords, page, content.title]."""
    n_parts: list[str] = []
    toks: list[str] = []
    real = 0
    for i, raw in enumerate(sources):
        if not isinstance(raw, str) or raw == "":
            continue
        raw = raw[:KW_MAX_SOURCE_CHARS]
        s = norm_page(raw) if i == 2 else norm_text(raw)
        if s == "":
            continue
        n_parts.append(s)
        if real >= KW_MAX_TOKENS:
            continue
        groups = s.split(",") if i in (0, 1) else [s]
        for g in groups:
            gt = tokenize(g)
            if not gt:
                continue
            if real >= KW_MAX_TOKENS:
                break
            if toks:
                toks.append(_KW_SEP)
            for tk in gt:
                if real >= KW_MAX_TOKENS:
                    break
                toks.append(tk)
                real += 1
    return "\n".join(n_parts), toks, " ".join(toks)


def prepare_kw_keys(words: list) -> list[tuple[str, list[str]]]:
    """Подготовка списка слов: (nk, токены). Пустые, слишком длинные и
    слишком «фразовые» слова отбрасываются; список режется до KW_MAX_WORDS."""
    out: list[tuple[str, list[str]]] = []
    for w in list(words)[:KW_MAX_WORDS]:
        if not isinstance(w, str) or len(w) > KW_MAX_WORD_CHARS:
            continue
        toks = tokenize(norm_text(w))
        if not toks or len(toks) > KW_MAX_PHRASE_TOKENS:
            continue
        out.append((" ".join(toks), toks))
    return out


def _kw_substring(key: tuple[str, list[str]], text: tuple[str, list[str], str]) -> bool:
    return key[0] in text[0] or key[0] in text[2]


def _kw_window(key: tuple[str, list[str]], text: tuple[str, list[str], str], eq) -> bool:
    kt, tt = key[1], text[1]
    for i in range(len(tt) - len(kt) + 1):
        if all(eq(w, tt[i + j]) for j, w in enumerate(kt)):
            return True
    return False


def kw_allow_match(mode: str, keys: list, text: tuple[str, list[str], str]) -> bool:
    """Хотя бы один разрешающий ключ совпал по режиму. Пустой текст — False."""
    if text[0] == "":
        return False
    for key in keys:
        if mode == "word_start":
            ok = _kw_window(key, text, lambda k, t: t.startswith(k))
        elif mode == "whole_word":
            ok = _kw_window(key, text, lambda k, t: k == t)
        else:
            ok = _kw_substring(key, text)
        if ok:
            return True
    return False


def kw_block_match(keys: list, text: tuple[str, list[str], str]) -> bool:
    """Стоп-слова всегда широко (как substring), независимо от режима."""
    if text[0] == "":
        return False
    return any(_kw_substring(k, text) for k in keys)


def eval_keywords(mode: str, sources: list[str], allow: list, block: list) -> tuple[bool, bool]:
    """(allow_ok, blocked) для заданных источников. allow_ok = True, если
    разрешающих слов после очистки нет. mode пусто/неизвестно — легаси-путь."""
    if mode not in KW_MODES:
        hay = " ".join(x if isinstance(x, str) else "" for x in sources).lower()
        allow_ok = not allow or any(k.lower() in hay for k in allow)
        blocked = bool(block) and any(k.lower() in hay for k in block)
        return allow_ok, blocked
    akeys, bkeys = prepare_kw_keys(allow), prepare_kw_keys(block)
    text = build_kw_text(sources)
    allow_ok = not akeys or kw_allow_match(mode, akeys, text)
    blocked = bool(bkeys) and kw_block_match(bkeys, text)
    return allow_ok, blocked
