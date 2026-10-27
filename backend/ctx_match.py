"""Контекстный матчинг таргетинга: категории площадки (IAB).

Python-эталон для Go-биддера (bidder-go/internal/targeting/iab.go). Правила и
общие тест-векторы — bidder-go/internal/targeting/testdata/match_cases.json:
оба набора тестов читают один файл, расхождение валит CI с обеих сторон.
"""

from __future__ import annotations

from typing import Any

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
