"""B13g: нормализация уведомлений ЕРИР без БД — маппинг objectType Ozon,
разбор дат, mock-очередь с курсором, тело запроса error_list."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from ord.mock import MockOrdAdapter
from ord.notifications import _parse_ts, normalize_object_type
from ord.ozon import OzonOrdClient


def test_object_type_normalization():
    assert normalize_object_type("CREATIVE") == "creative"
    assert normalize_object_type("organisation") == "organisation"
    assert normalize_object_type("SOMETHING") == "unknown"
    assert normalize_object_type(None) == "unknown"


def test_parse_ts_accepts_zulu_and_none():
    assert _parse_ts("2026-09-11T10:00:00Z").tzinfo is not None
    assert _parse_ts(None) is None
    assert _parse_ts("not-a-date") is None


def test_mock_queue_respects_cursor_and_page(monkeypatch):
    monkeypatch.setattr(MockOrdAdapter, "notifications", [{"id": i} for i in (3, 1, 2, 5, 4)])
    out = asyncio.run(MockOrdAdapter().list_error_notifications(gt_id=2, page_size=2))
    assert [n["id"] for n in out] == [3, 4]


def test_ozon_error_list_request_and_mapping():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"notification": [
            {"id": "12", "objectId": "o-1", "externalId": "abc:v2", "objectType": "CREATIVE",
             "text": "bad", "date": "2026-09-11T10:00:00Z", "updatedAt": None, "url": "https://x"},
            {"objectId": "no-id"},
        ]})

    client = OzonOrdClient(api_key="k", base_url="https://ord.test")
    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient

    class _Client(orig):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    httpx.AsyncClient = _Client
    try:
        out = asyncio.run(client.list_error_notifications(gt_id=7, page_size=50))
    finally:
        httpx.AsyncClient = orig
    assert seen["path"] == "/api/external/notification/error_list"
    assert json.loads(seen["body"]) == {"pageSize": 50, "gtNotificationId": 7}
    assert out == [{"id": 12, "object_id": "o-1", "external_id": "abc:v2", "object_type": "CREATIVE",
                    "text": "bad", "date": "2026-09-11T10:00:00Z", "updated_at": None, "url": "https://x"}]
