"""HTTP-проба для ops/preprod_check.sh. Выполняется ВНУТРИ контейнера backend
(`docker compose exec -T backend python - ... < ops/lib/http_probe.py`), чтобы
ходить по именам compose-сети (backend:8000, bidder-go:8090, frontend:3000) —
в production host-порты сняты (compose.prod.yml), и localhost с хоста не
указывает ни на один сервис.

Только stdlib: в образе backend нет curl. Никогда не бросает исключение
наружу — сетевые ошибки возвращаются как status=0 + error, чтобы вызывающий
скрипт мог отличить «сервис недоступен» (NOT TESTED для зависимых проверок)
от «сервис ответил неправильно» (FAIL).

    python - METHOD URL [-H 'Name: value']... [--body STR | --body-file PATH |
             --body-size N] [--timeout SEC] [--max-body BYTES]

stdout — одна JSON-строка:
    {"status": 200, "headers": {"content-type": "..."}, "body": "...",
     "body_bytes": 1234, "error": null, "elapsed_ms": 12.3}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("method")
    p.add_argument("url")
    p.add_argument("-H", "--header", action="append", default=[])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--body")
    g.add_argument("--body-file")
    g.add_argument("--body-size", type=int, help="сгенерировать JSON-тело указанного размера (проверка 413)")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--max-body", type=int, default=64 * 1024, help="сколько байт ответа вернуть в body")
    a = p.parse_args(argv)

    data: bytes | None = None
    if a.body is not None:
        data = a.body.encode()
    elif a.body_file:
        with open(a.body_file, "rb") as fh:
            data = fh.read()
    elif a.body_size:
        # Валидный JSON, чтобы 413 был из-за размера, а не парсинга: {"id":"aaaa..."}
        pad = max(0, a.body_size - len('{"id":""}'))
        data = ('{"id":"' + "a" * pad + '"}').encode()

    headers = {}
    for h in a.header:
        name, _, value = h.partition(":")
        headers[name.strip()] = value.strip()
    if data is not None and "Content-Type" not in {k.title() for k in headers}:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(a.url, data=data, method=a.method.upper(), headers=headers)
    out = {"status": 0, "headers": {}, "body": "", "body_bytes": 0, "error": None, "elapsed_ms": 0.0}
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=a.timeout) as resp:  # noqa: S310 — внутренняя сеть compose
            raw = resp.read()
            out["status"] = resp.status
            out["headers"] = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp is not None else b""
        out["status"] = e.code
        out["headers"] = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
    except Exception as e:  # noqa: BLE001 — URLError, timeout, RemoteDisconnected, ConnectionReset...
        raw = b""
        out["error"] = f"{type(e).__name__}: {e}"
    out["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
    out["body_bytes"] = len(raw)
    out["body"] = raw[: a.max_body].decode("utf-8", "replace")
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
