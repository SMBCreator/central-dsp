"""Юнит-тесты чистых функций отчёта скоринга (без БД)."""
import pytest
from fastapi import HTTPException

from routers import scoring as r


ROWS = [
    {"domain": "a.ru", "score": 0.9, "impressions": 100, "blocked": False, "flagged": False},
    {"domain": "b.ru", "score": None, "impressions": 500, "blocked": False, "flagged": False},
    {"domain": "c.ru", "score": 0.2, "impressions": 300, "blocked": True, "flagged": True},
    {"domain": "d.ru", "score": 0.4, "impressions": 50, "blocked": False, "flagged": True},
]


def test_sort_none_last_asc_and_desc():
    asc = [x["domain"] for x in r._sort_rows(ROWS, "score", "asc")]
    desc = [x["domain"] for x in r._sort_rows(ROWS, "score", "desc")]
    assert asc == ["c.ru", "d.ru", "a.ru", "b.ru"]
    assert desc == ["a.ru", "d.ru", "c.ru", "b.ru"]


def test_sort_by_domain():
    assert [x["domain"] for x in r._sort_rows(ROWS, "domain", "desc")][0] == "d.ru"


def test_filters():
    assert [x["domain"] for x in r._apply_filters(ROWS, "C.", False, False)] == ["c.ru"]
    assert len(r._apply_filters(ROWS, None, True, False)) == 1
    assert len(r._apply_filters(ROWS, None, False, True)) == 2


@pytest.mark.parametrize("raw,expected", [
    ("Example.RU", "example.ru"),
    ("https://www.site.com/path?x=1", "site.com"),
    ("host.io:8080", "host.io"),
])
def test_norm_domain(raw, expected):
    assert r._norm_domain(raw) == expected


@pytest.mark.parametrize("raw", ["", "localhost", "bad domain", "a..b"])
def test_norm_domain_rejects(raw):
    with pytest.raises(HTTPException):
        r._norm_domain(raw)


def test_cache_invalidation_by_user():
    r._report_cache.clear()
    r._report_cache["u1|domain|||"] = (0.0, {}, [])
    r._report_cache["u2|domain|||"] = (0.0, {}, [])
    r.invalidate_report_cache("u1")
    assert list(r._report_cache) == ["u2|domain|||"]
    r.invalidate_report_cache()
    assert not r._report_cache
