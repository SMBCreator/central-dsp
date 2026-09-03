"""Unit-тесты валидации загрузки файлов креативов (magic bytes, размеры, лимиты).

Без Postgres: проверяем чистые функции и HTTP-эндпоинт с подменой зависимостей.
"""
import io
import struct
import zlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.creatives as cr
from routers.creatives import image_dimensions, sniff_upload


def _png(w: int, h: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + chunk
        + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    )


def _gif(w: int, h: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00" * 10


def _jpeg(w: int, h: int) -> bytes:
    # SOI + APP0 (пустой) + SOF0 с размерами
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", h, w) + b"\x01\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\x00" * 16


def _webp_vp8x(w: int, h: int) -> bytes:
    body = b"WEBPVP8X" + struct.pack("<I", 10) + b"\x00" * 4 + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", len(body)) + body + b"\x00" * 8


def _mp4() -> bytes:
    return struct.pack(">I", 24) + b"ftypisom" + b"\x00" * 16


def _webm() -> bytes:
    return b"\x1a\x45\xdf\xa3" + b"\x00" * 32


# ---------------------------------------------------------------------------
# sniff / dimensions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "data, mime",
    [
        (_png(1, 1), "image/png"),
        (_gif(1, 1), "image/gif"),
        (_jpeg(1, 1), "image/jpeg"),
        (_webp_vp8x(1, 1), "image/webp"),
        (_mp4(), "video/mp4"),
        (_webm(), "video/webm"),
    ],
)
def test_sniff_known_types(data, mime):
    assert sniff_upload(data) == mime


@pytest.mark.parametrize(
    "data",
    [
        b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        b"<html><script>alert(1)</script></html>",
        b"%PDF-1.4",
        b"",
        b"\x00" * 64,
        b"GIF8",  # обрезанная сигнатура
    ],
)
def test_sniff_rejects_unknown(data):
    assert sniff_upload(data) is None


def test_dimensions_png():
    assert image_dimensions(_png(300, 250)) == (300, 250)


def test_dimensions_gif():
    assert image_dimensions(_gif(728, 90)) == (728, 90)


def test_dimensions_jpeg():
    assert image_dimensions(_jpeg(970, 250)) == (970, 250)


def test_dimensions_webp_vp8x():
    assert image_dimensions(_webp_vp8x(160, 600)) == (160, 600)


def test_dimensions_truncated_returns_none():
    assert image_dimensions(b"\x89PNG\r\n\x1a\n\x00\x00") is None


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(cr, "_MAX_IMAGE_BYTES", 1024)  # 1 КБ — чтобы проверить 413 быстро
    monkeypatch.setattr(cr, "_MAX_VIDEO_BYTES", 4096)
    app = FastAPI()
    app.include_router(cr.router)
    app.dependency_overrides[cr.require_creative_manager] = lambda: {"id": "u1", "email": "u@x", "role": "admin"}
    return TestClient(app), tmp_path


def _post(client, name, data, ctype):
    return client.post("/creatives/upload", files={"file": (name, io.BytesIO(data), ctype)})


def test_upload_png_ok(client):
    c, d = client
    r = _post(c, "a.png", _png(300, 250), "image/png")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mime"] == "image/png"
    assert body["width"] == 300 and body["height"] == 250
    assert body["path"].startswith("/uploads/") and body["path"].endswith(".png")
    assert (d / body["path"].rsplit("/", 1)[1]).exists()


def test_upload_content_type_is_ignored_extension_from_magic(client):
    """Клиент прислал image/png, но байты — GIF: расширение должно быть .gif."""
    c, _ = client
    r = _post(c, "fake.png", _gif(10, 10), "image/png")
    assert r.status_code == 200
    assert r.json()["mime"] == "image/gif"
    assert r.json()["path"].endswith(".gif")


def test_upload_rejects_svg_disguised_as_png(client):
    c, d = client
    r = _post(c, "x.png", b"<svg onload=alert(1)></svg>", "image/png")
    assert r.status_code == 415
    assert not any(d.iterdir())


def test_upload_rejects_empty(client):
    c, _ = client
    r = _post(c, "e.png", b"", "image/png")
    assert r.status_code == 400


def test_upload_image_over_limit_413_and_cleaned(client):
    c, d = client
    r = _post(c, "big.png", _png(1, 1) + b"\x00" * 5000, "image/png")
    assert r.status_code == 413
    assert "лимит" in r.json()["detail"].lower()
    assert not any(d.iterdir()), "файл должен быть удалён при превышении лимита"


def test_upload_video_uses_video_limit(client):
    c, _ = client
    # 3 КБ видео: больше лимита картинок (1 КБ), но меньше лимита видео (4 КБ)
    r = _post(c, "v.mp4", _mp4() + b"\x00" * 3000, "video/mp4")
    assert r.status_code == 200
    assert r.json()["mime"] == "video/mp4"
    assert r.json()["width"] is None


def test_delete_upload_bad_name_400(client):
    c, _ = client
    r = c.delete("/creatives/upload/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404)
    r = c.delete("/creatives/upload/notahex.png")
    assert r.status_code == 400
