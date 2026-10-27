"""ТЗ batch creatives п.2: справочник размеров баннеров и GET /creatives/sizes.

Без БД: эндпоинт проверяется на in-process приложении с подменой current_user.
"""
import asyncio
import re
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ad_sizes  # noqa: E402
import routers.creatives as crs  # noqa: E402
from auth import current_user  # noqa: E402
from routers.simulator import SIM_BANNER_SIZES  # noqa: E402

FRONTEND_COPY = Path(__file__).resolve().parents[2] / "frontend" / "lib" / "ad-sizes-catalog.ts"


def test_catalog_has_24_unique_sizes():
    keys = [s["size"] for s in ad_sizes.AD_SIZES]
    assert len(keys) == 24
    assert len(set(keys)) == 24
    for want in ("120x600", "240x600", "300x500", "320x50", "320x100", "970x90", "970x250"):
        assert want in keys
    assert "20x600" not in keys


def test_catalog_entries_are_consistent():
    for s in ad_sizes.AD_SIZES:
        assert s["size"] == f"{s['width']}x{s['height']}"
        assert s["category"] in {"desktop", "mobile", "universal"}
        assert s["label"]
        assert s["max_bytes"] is None or s["max_bytes"] > 0


def test_nearest_sizes():
    assert ad_sizes.nearest_sizes(301, 250)[0] == "300x250"
    assert len(ad_sizes.nearest_sizes(301, 250)) == 3
    assert ad_sizes.nearest_sizes(0, 0) == []


def test_simulator_sizes_are_in_catalog():
    assert set(SIM_BANNER_SIZES) <= ad_sizes.ALLOWED_SIZES


def test_size_strict_env(monkeypatch):
    monkeypatch.delenv("CREATIVE_SIZE_STRICT", raising=False)
    assert ad_sizes.size_strict() is True
    monkeypatch.setenv("CREATIVE_SIZE_STRICT", "0")
    assert ad_sizes.size_strict() is False


class TestAdSizesParity:
    """Две копии справочника (backend/ad_sizes.py ↔ frontend/lib/ad-sizes-catalog.ts) — один тест."""

    ENTRY_RE = re.compile(
        r'\{ size: "(\d+x\d+)", width: (\d+), height: (\d+), label: "([^"]+)", '
        r'category: "(\w+)", max_bytes: (null|\d+) \}'
    )

    def test_frontend_matches_backend(self):
        if not FRONTEND_COPY.exists():
            import pytest

            pytest.skip("frontend not available")
        ts = [
            {"size": m[0], "width": int(m[1]), "height": int(m[2]), "label": m[3], "category": m[4],
             "max_bytes": None if m[5] == "null" else int(m[5])}
            for m in self.ENTRY_RE.findall(FRONTEND_COPY.read_text(encoding="utf-8"))
        ]
        assert ts == ad_sizes.AD_SIZES


def _client(user: dict | None) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(crs.router)
    if user is not None:
        async def _fake_user():
            return user

        app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_sizes_endpoint_requires_auth():
    async def scenario():
        async with _client(None) as c:
            r = await c.get("/creatives/sizes")
            assert r.status_code == 401

    asyncio.run(scenario())


def test_sizes_endpoint_returns_catalog():
    user = {"id": "u1", "email": "m@x", "role": "manager", "status": "active", "permissions": {}}

    async def scenario():
        async with _client(user) as c:
            r = await c.get("/creatives/sizes")
            assert r.status_code == 200
            data = r.json()
            assert len(data) == 24
            assert {"size", "width", "height", "label", "category", "max_bytes"} <= set(data[0])
            assert [d["size"] for d in data] == [s["size"] for s in ad_sizes.AD_SIZES]

    asyncio.run(scenario())
