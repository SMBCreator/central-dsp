"""D6: матрица RBAC по каждому смонтированному маршруту.

Дополняет test_rbac_matrix.py (guard-функции сами по себе): здесь проверяется,
что guard'ы реально стоят на маршрутах. Три уровня, все без БД:
1. статические инварианты по цепочке Depends (scripts/rbac_matrix.py);
2. docs/audit/rbac_matrix.md совпадает с тем, что реально смонтировано;
3. живой прогон: read-only роли (viewer/analyst/finance/client без флагов)
   бьют в КАЖДЫЙ мутирующий маршрут и получают 403 (или 404/405),
   но никогда 2xx и никогда 422 — 422 значило бы, что валидация тела
   сработала раньше guard'а, т.е. guard'а на маршруте нет.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import rbac_matrix as rm  # noqa: E402
from auth import current_user  # noqa: E402

import main  # noqa: E402

ROWS = rm.build_rows(main.app)

READ_ONLY_USERS = {
    "viewer": {"id": "00000000-0000-0000-0000-00000000000a", "role": "viewer", "permissions": {}},
    "analyst": {"id": "00000000-0000-0000-0000-00000000000b", "role": "analyst", "permissions": {}},
    "finance": {"id": "00000000-0000-0000-0000-00000000000c", "role": "finance", "permissions": {}},
    "client": {"id": "00000000-0000-0000-0000-00000000000d", "role": "client", "permissions": {}},
}
for u in READ_ONLY_USERS.values():
    u.update({"email": f"{u['role']}@x", "name": u["role"], "status": "active"})


def test_static_invariants():
    problems = rm.violations(ROWS)
    assert not problems, "\n".join(problems)


def test_matrix_doc_is_current():
    expected = rm.render(ROWS)
    actual = rm.DOC_PATH.read_text(encoding="utf-8") if rm.DOC_PATH.exists() else ""
    assert actual == expected, "docs/audit/rbac_matrix.md устарел: python backend/scripts/rbac_matrix.py --write"


def _client_as(user: dict) -> httpx.AsyncClient:
    # Те же роутеры в свежем приложении без middleware main.app (viewer-guard и
    # rate-limit ходят в БД/Redis); include_router привязывает overrides к app.
    app = FastAPI()
    for router in rm.included_routers(main.app):
        app.include_router(router)
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _fill(path: str) -> str:
    out = path
    while "{" in out:
        i, j = out.index("{"), out.index("}")
        out = out[:i] + "00000000-0000-0000-0000-000000000001" + out[j + 1:]
    return out


def _guarded_mutating_rows():
    for row in ROWS:
        if row["method"] in rm.MUTATING and row["kind"] != "none":
            yield row


@pytest.mark.parametrize("role", list(READ_ONLY_USERS))
def test_read_only_roles_cannot_mutate(role):
    user = READ_ONLY_USERS[role]

    async def run():
        failures = []
        async with _client_as(user) as c:
            for row in _guarded_mutating_rows():
                if row["kind"] == "session":
                    continue  # self-service (CURRENT_USER_ONLY): разрешено любой роли по дизайну
                if role == "finance" and row["path"].startswith("/billing"):
                    continue  # FINANCE_WRITE — единственная запись, разрешённая finance
                resp = await c.request(row["method"], _fill(row["path"]), json={})
                if resp.status_code not in (403, 404, 405):
                    failures.append(f"{role}: {row['method']} {row['path']} -> {resp.status_code}")
        return failures

    failures = asyncio.run(run())
    assert not failures, "\n".join(failures)


def test_every_mutating_route_is_classified():
    """Подстраховка: ни один мутирующий маршрут не выпал из проверки выше."""
    unchecked = [
        f"{r['method']} {r['path']}"
        for r in ROWS
        if r["method"] in rm.MUTATING and r["kind"] == "none" and rm.no_cookie_reason(r["path"]) is None
    ]
    assert not unchecked, "\n".join(unchecked)
