"""Контракт UI -> API: каждый вызов `api(...)` / `useSWR(...)` во фронтенде
должен попадать в существующий маршрут FastAPI с тем же методом.

Зачем: mock-api во фронтенде принимает почти любые пути, поэтому опечатка в
URL (`/pixel` вместо `/pixels`, `/rotate-token` вместо `/token`) живёт до
первого клика на проде. Три такие нашли 2026-09-05.

Ограничения сканера (осознанные): берём только литеральные и template-строки,
начинающиеся с `/`; `${...}` внутри пути считаем одним сегментом; query-string
отбрасываем. Динамические пути, собранные из переменных целиком, не видны —
их немного, и они покрываются e2e.
"""

import re
from pathlib import Path

import pytest

import main

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"

# `api<T>(`/path/${x}`, { method: "DELETE", ... })` — метод ищем в первых ~200
# символах после URL, чтобы не поймать method из соседнего вызова.
_API_CALL = re.compile(
    r"""\bapi(?:<[^>()]*>)?\(\s*([`"'])(/[^`"']*)\1(?P<rest>\s*,\s*\{(?:(?!\)\s*\n)[^}]){0,200})?""",
    re.S,
)
_METHOD = re.compile(r'method:\s*"(GET|POST|PUT|PATCH|DELETE)"')
# useSWR("/x") / useSWR<T>(cond ? "/x" : null) / useSWR(`/x?${qs}`)
_SWR_CALL = re.compile(r"""useSWR(?:<[^>()]*>)?\(\s*(?:[^,`"'\n]*?\?\s*)?([`"'])(/[^`"']*)\1""")
# downloadCsv(`/scoring/report.csv?...`)
_CSV_CALL = re.compile(r"""downloadCsv\(\s*([`"'])(/[^`"']*)\1""")

# Маршруты, объявленные с include_in_schema=False или снаружи FastAPI.
_EXTRA_ROUTES = {("GET", "/metrics"), ("GET", "/health")}

# Пути, где переменная стоит на месте статического сегмента (не {param}):
# указываем, какие значения она принимает — проверяем каждое.
_SEGMENT_EXPANSIONS = {
    "/ord/${kind}/${id}/register": ["/ord/counterparties/${id}/register", "/ord/contracts/${id}/register"],
}


def _frontend_sources():
    for sub in ("app", "components", "lib"):
        for f in (FRONTEND / sub).rglob("*.ts*"):
            if f.name.endswith(".d.ts") or "mock-api" in f.name or f.name.endswith(".test.tsx"):
                continue
            yield f


def _frontend_calls() -> set[tuple[str, str, str]]:
    calls: set[tuple[str, str, str]] = set()
    for f in _frontend_sources():
        src = f.read_text(encoding="utf-8")
        rel = str(f.relative_to(FRONTEND))
        for m in _API_CALL.finditer(src):
            method = "GET"
            if m.group("rest"):
                mm = _METHOD.search(m.group("rest"))
                if mm:
                    method = mm.group(1)
            calls.add((method, m.group(2), rel))
        for m in _SWR_CALL.finditer(src):
            calls.add(("GET", m.group(2), rel))
        for m in _CSV_CALL.finditer(src):
            calls.add(("GET", m.group(2), rel))
    return calls


def _backend_routes() -> list[tuple[str, re.Pattern[str]]]:
    schema = main.app.openapi()
    pairs = {(m.upper(), p) for p, ops in schema["paths"].items() for m in ops} | _EXTRA_ROUTES
    out = []
    for method, path in pairs:
        pattern = "^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)) + "$"
        out.append((method, re.compile(pattern)))
    return out


def _normalize(url: str) -> str:
    url = url.split("?", 1)[0]
    # `${encodeURIComponent(x)}` и любые `${...}` — один сегмент пути.
    url = re.sub(r"\$\{[^}]*\}", "__p__", url)
    return url.rstrip("/") or "/"


@pytest.fixture(scope="module")
def routes():
    return _backend_routes()


def test_frontend_calls_hit_existing_backend_routes(routes):
    calls = _frontend_calls()
    assert len(calls) > 100, f"scanner found too few calls ({len(calls)}) — regexes broke?"
    missing = []
    for method, url, where in sorted(calls):
        for variant in _SEGMENT_EXPANSIONS.get(url.split("?", 1)[0], [url]):
            path = _normalize(variant)
            if "__p__" in path and path.replace("__p__", "").strip("/") == "":
                continue  # весь путь из переменной — сканеру не виден по определению
            if not any(m == method and rx.match(path) for m, rx in routes):
                missing.append(f"{method} {variant}  ({where})")
    assert not missing, "frontend calls without a backend route:\n  " + "\n  ".join(missing)


@pytest.mark.parametrize(
    "method,url",
    [
        ("GET", "/pixels"),
        ("POST", "/supply-partners/${p.id}/token"),
        ("DELETE", "/scoring/blocks/${encodeURIComponent(campaignId)}/${encodeURIComponent(row.domain)}"),
    ],
)
def test_regressions_fixed_2026_09_05(routes, method, url):
    path = _normalize(url)
    assert any(m == method and rx.match(path) for m, rx in routes), f"{method} {url}"
