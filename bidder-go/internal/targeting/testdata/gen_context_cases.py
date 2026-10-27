"""Генератор context_cases.json: матрица «тематика × слова × режим × опция без тематики × стоп-условия».

Ожидание считает оракул ниже прямо по ТЗ (§5.2, §5.4), независимо от Go и Python.
Порядок проверок повторяет порядок отказов в матчерах: IAB-разрешение (all) ->
iab_block -> keywords_block -> слова / объединённая часть.
Запуск: python3 gen_context_cases.py > context_cases.json
"""
import itertools
import json

CATS = {"none": None, "match": ["IAB1"], "other": ["IAB9"]}
TEXTS = {"hit": "Ипотека в Москве", "miss": "Погода завтра", "block": "Ипотека, порно"}
KW = {"none": None, "legacy": ("ипотек", None), "v2": ("ипотек", "word_start")}


def oracle(cats, text, has_iab, kw, unknown, mode, iab_block, kw_block):
    has_cats = CATS[cats] is not None
    iab_hit = cats == "match"
    kw_hit = text in ("hit", "block")
    has_kw = KW[kw] is not None
    any_mode = mode == "any"
    combined = any_mode and has_iab and has_kw
    if has_iab and not combined and not iab_hit and not (unknown and not any_mode and not has_cats):
        return False, "inventory_iab"
    if iab_block and iab_hit:
        return False, "inventory_iab"
    if kw_block and text == "block":
        return False, "keywords"
    if combined:
        return (iab_hit or kw_hit), ("" if (iab_hit or kw_hit) else "context")
    if has_kw and not kw_hit:
        return False, "keywords"
    return True, ""


def build(cats, text, has_iab, kw, unknown, mode, iab_block, kw_block):
    tg = {}
    if has_iab:
        tg["iab_categories"] = ["IAB1"]
    if iab_block:
        tg["iab_block"] = ["IAB1"]
    if KW[kw] is not None:
        tg["keywords_allow"] = [KW[kw][0]]
        if KW[kw][1]:
            tg["keywords_match"] = KW[kw][1]
    if kw_block:
        tg["keywords_block"] = ["порно"]
    if unknown:
        tg["iab_allow_unknown"] = True
    if mode:
        tg["context_mode"] = mode
    site = {"page": "https://shop.example/page", "content": {"title": TEXTS[text]}}
    if CATS[cats] is not None:
        site["cat"] = CATS[cats]
    return tg, {"site": site}


cases = []
for cats, text, has_iab, kw, unknown, mode in itertools.product(
    CATS, ("hit", "miss"), (True, False), KW, (False, True), ("", "all", "any")
):
    cases.append((cats, text, has_iab, kw, unknown, mode, False, False))
# стоп-условия в обоих режимах: блок срабатывает первым при любых прочих флагах
for cats, text, kw, mode, (ib, kb) in itertools.product(
    CATS, ("hit", "block"), ("none", "legacy", "v2"), ("", "all", "any"), ((True, False), (False, True), (True, True))
):
    cases.append((cats, text, True, kw, True, mode, ib, kb))

out = []
for c in cases:
    cats, text, has_iab, kw, unknown, mode, ib, kb = c
    tg, req = build(*c)
    want, reason = oracle(*c)
    name = f"cats={cats} text={text} iab={int(has_iab)} kw={kw} unknown={int(unknown)} mode={mode or '-'} iab_block={int(ib)} kw_block={int(kb)}"
    out.append({"name": name, "targeting": tg, "req": req, "want": want, "reason": reason})
print(json.dumps({"match": out}, ensure_ascii=False, indent=1))
