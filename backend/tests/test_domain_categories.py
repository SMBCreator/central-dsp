"""Справочник «домен → тематика»: чистая логика (без БД и Redis)."""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import domain_categories as dc  # noqa: E402


def test_normalize_domain():
    assert dc.normalize_domain("WWW.Example.COM.") == "example.com"
    assert dc.normalize_domain("https://www.news.ru/path?x=1") == "news.ru"
    assert dc.normalize_domain("site.ru:8080") == "site.ru"
    assert dc.normalize_domain("пример.рф") == "xn--e1afmkfd.xn--p1ai"
    for bad in ("", "localhost", "a b.ru", "user@x.ru", "1.2.3.4", "-x.ru", "x..ru", None, 5, "a" * 300 + ".ru"):
        assert dc.normalize_domain(bad) is None, bad


def test_normalize_codes():
    codes, bad = dc.normalize_codes(["iab1", "IAB1-2", "IAB1", "bogus", "IAB"])
    assert codes == ["IAB1", "IAB1-2"]
    assert bad == ["bogus", "IAB"]
    _, bad = dc.normalize_codes([f"IAB{i}" for i in range(1, 12)])
    assert bad and "максимум" in bad[0]


def test_parse_import_csv_valid_and_errors():
    text = "domain;iab\nWww.A.ru;IAB1,IAB2-1\nb.ru; iab3\r\nbad_domain;IAB1\nc.ru;nope\nd.ru;\na.ru;IAB5\n\n"
    rows, errors = dc.parse_import_csv(text)
    assert rows == [{"domain": "a.ru", "iab_codes": ["IAB1", "IAB2-1"]}, {"domain": "b.ru", "iab_codes": ["IAB3"]}]
    by_line = {e["line"]: e["error"] for e in errors}
    assert "некорректное имя" in by_line[4]
    assert "недопустимые коды" in by_line[5]
    assert "не указаны" in by_line[6]
    assert "дубликат" in by_line[7]


def test_parse_import_csv_row_limit():
    text = "\n".join(f"d{i}.ru;IAB1" for i in range(dc.MAX_IMPORT_ROWS + 1))
    rows, errors = dc.parse_import_csv(text)
    assert rows == [] and len(errors) == 1 and "слишком много" in errors[0]["error"]
    assert len(dc.parse_import_csv("\n".join(f"d{i}.ru;IAB1" for i in range(dc.MAX_IMPORT_ROWS)))[0]) == dc.MAX_IMPORT_ROWS


def test_merge_learned_codes_limit_and_order():
    got = dc.merge_learned_codes(["IAB9"], Counter({"IAB1": 5, "IAB2": 9, "IAB3": 1}))
    assert got == ["IAB2", "IAB1", "IAB3", "IAB9"]
    many = Counter({f"IAB{i}": i for i in range(1, 12)})
    assert len(dc.merge_learned_codes([], many)) == dc.MAX_CODES_PER_DOMAIN
    assert dc.merge_learned_codes([], many)[0] == "IAB11"


def test_parse_learn_entries_skips_junk():
    got = dc.parse_learn_entries({
        "site.ru": "4", "site.ru|IAB1": "3", "site.ru|IAB2-1": "1", "site.ru|junk": "9",
        "bad_domain|IAB1": "2", "only-hits.ru": "7", "neg.ru|IAB1": "-1", "nan.ru|IAB1": "x",
    })
    assert set(got) == {"site.ru"}
    assert got["site.ru"]["hits"] == 4
    assert got["site.ru"]["cats"] == Counter({"IAB1": 3, "IAB2-1": 1})
