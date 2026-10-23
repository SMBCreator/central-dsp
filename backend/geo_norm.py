"""Нормализация гео-названий для GEO v2 (docs/GEO.md).

Одна функция на все слои: импорт справочника (ops/geo_import.py) кладёт в
geo_places.aliases уже нормализованные строки, backend/geo_v2.py и Go-биддер
(bidder-go/internal/geo/normalize.go) нормализуют значения из bid request тем
же правилом и сравнивают точным map lookup — без fuzzy-поиска в hot path.

Паритет Python/Go закреплён общими кейсами
bidder-go/internal/geo/testdata/fixtures.json (раздел "normalize").
Меняя правило, обновите обе реализации и пересоберите справочник
(aliases хранятся нормализованными).
"""

from __future__ import annotations

# Символы, которые превращаются в пробел (пунктуация и разделители).
_SPACE_CHARS = frozenset("\"'`’‘«»“”().,;:-–—_/\\\u00a0")

# Сокращения целыми словами (после разбиения на токены).
_TOKEN_EXPAND = {
    "обл": "область",
    "респ": "республика",
}

# Префиксы, которые SSP часто добавляют к городу.
_PREFIXES = ("г ", "город ", "city of ")


def geo_normalize(value: str | None) -> str:
    if not value:
        return ""
    # Python lower() превращает "İ" в "i" + combining dot, Go ToLower — в "i".
    # Сводим заранее, чтобы "İstanbul" совпадал в обеих реализациях.
    # Конечную сигму Python выбирает по позиции ("ΑΘΗΝΑΣ" -> "αθηνας"), Go — нет.
    s = value.replace("İ", "i").lower().replace("ё", "е").replace("ς", "σ")
    s = "".join(" " if ch in _SPACE_CHARS else ch for ch in s)
    tokens = [_TOKEN_EXPAND.get(t, t) for t in s.split()]
    s = " ".join(tokens)
    for prefix in _PREFIXES:
        if s.startswith(prefix) and len(s) > len(prefix):
            s = s[len(prefix):]
            break
    return s
