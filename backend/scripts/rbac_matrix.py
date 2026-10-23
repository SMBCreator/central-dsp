"""Матрица RBAC по каждому маршруту бэкенда (чеклист D6).

Источник правды — сами объекты FastAPI: для каждого маршрута читаем цепочку
Depends и вычисляем, какие роли пройдут guard. Ничего не парсим из исходников.

    python scripts/rbac_matrix.py            # напечатать таблицу
    python scripts/rbac_matrix.py --write    # обновить docs/audit/rbac_matrix.md
    python scripts/rbac_matrix.py --check    # exit 1, если файл устарел (используется тестом)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
os.environ.setdefault("JWT_SECRET", "matrix-only-" + "x" * 40)

from fastapi.routing import APIRoute  # noqa: E402

import rbac  # noqa: E402

DOC_PATH = BACKEND.parent / "docs" / "audit" / "rbac_matrix.md"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# Маршруты без cookie-сессии: у каждого свой механизм, указан явно.
NO_COOKIE_AUTH = {
    "/auth/login": "публично",
    "/auth/register": "публично (REGISTRATION_MODE)",
    "/auth/logout": "публично (гасит cookie)",
    "/health": "публично (liveness, Docker HEALTHCHECK)",
    "/ready": "публично (readiness: deploy gate, Prometheus); статусы без секретов",
    "/metrics": "публично (сеть Prometheus)",
    "/share/public/{token}": "share-токен в URL",
    "/pixel/collect": "публично (пиксель), rate-limit",
    "/pixel/script/{pixel_id}.js": "публично (пиксель)",
}
NO_COOKIE_AUTH_PREFIX = {
    "/cron/": "Bearer CRON_SECRET",
    "/alerts/webhook": "Bearer ALERT_WEBHOOK_SECRET (Alertmanager)",
    "/rtb-feed/": "Bearer RTB_API_TOKEN",
    "/rtb/": "endpoint_key / event-токен (HMAC)",
    "/sync/": "публично (ID sync, ID_SYNC_ENABLED; только cookie dsp_uid, rate-limit, /out — HMAC)",
}

# Мутирующие маршруты только с current_user: проверка прав внутри хендлера
# или self-service. Каждый — с обоснованием; новый такой маршрут ломает тест.
CURRENT_USER_ONLY = {
    ("POST", "/auth/change-password"): "self-service: только свой пароль",
    ("PATCH", "/auth/me"): "self-service: только свой профиль",
    ("POST", "/notifications/read-all"): "self-service: только свои уведомления",
    ("POST", "/notifications/{notification_id}/read"): "self-service: WHERE user_id = me",
    ("POST", "/simulator/run"): "владелец SSP-партнёра (WHERE user_id = me); engine=go (по умолчанию) — боевой Go-биддер, все запросы OpenRTB test=1 (события is_test); engine=python_reference — только вне production и с SIMULATOR_ALLOW_PYTHON_REFERENCE=1, без тихого фолбэка",
    ("POST", "/stats/report"): "чтение через POST (тело = фильтры), scope visible_user_ids",
    ("POST", "/geo/places/resolve"): "чтение через POST (тело = список названий до 500 строк): поиск по общему справочнику geo_places, без записи",
}

READ_ONLY_ROLES = ("viewer", "analyst")


def included_routers(app):
    """APIRouter'ы, подключённые через include_router (FastAPI монтирует их лениво)."""
    for r in app.routes:
        if type(r).__name__ == "_IncludedRouter":
            yield next(v for v in vars(r).values() if hasattr(v, "routes"))


def api_routes(app):
    for r in app.routes:
        if isinstance(r, APIRoute):
            yield r
    for router in included_routers(app):
        for sub in router.routes:
            if isinstance(sub, APIRoute):
                yield sub


def _closure(fn) -> dict:
    return {
        name: cell.cell_contents
        for name, cell in zip(fn.__code__.co_freevars, fn.__closure__ or ())
    }


def _walk(dependant, acc):
    for sub in dependant.dependencies:
        acc.append(sub.call)
        _walk(sub, acc)
    return acc


def guard_of(route: APIRoute) -> tuple[str, frozenset[str] | None, str]:
    """(вид guard'а, множество ролей или None, человекочитаемое описание)."""
    calls = _walk(route.dependant, [])
    names = {getattr(c, "__qualname__", "") for c in calls}
    for c in calls:
        q = getattr(c, "__qualname__", "")
        if q == "require_roles.<locals>.checker":
            roles = frozenset(_closure(c)["roles"])
            return "roles", roles, ", ".join(sorted(roles))
        if q == "require_campaign_edit.<locals>.checker":
            roles = frozenset(rbac.CAMPAIGN_WRITE)
            return "roles+flag", roles, ", ".join(sorted(roles)) + " + client[can_edit_campaigns]"
        if q == "_require_flag.<locals>.checker":
            cl = _closure(c)
            roles = frozenset(rbac._FLAG_IMPLICIT_ROLES) | frozenset(cl.get("extra_roles", ()))
            return "roles+flag", roles, ", ".join(sorted(roles)) + f" + client[{cl['flag']}]"
        if q == "require_billing_view.<locals>.checker":
            roles = frozenset(r for r in rbac.ALL_ROLES if rbac.can_view_billing({"role": r, "permissions": {}}))
            return "roles+flag", roles, ", ".join(sorted(roles)) + " + client[can_view_billing]"
    if "current_user" in names:
        return "session", None, "любая роль (scope внутри хендлера)"
    return "none", None, "—"


def build_rows(app) -> list[dict]:
    rows = []
    for r in api_routes(app):
        for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
            kind, roles, desc = guard_of(r)
            rows.append({"method": m, "path": r.path, "kind": kind, "roles": roles, "desc": desc})
    rows.sort(key=lambda x: (x["path"], x["method"]))
    return rows


def no_cookie_reason(path: str) -> str | None:
    if path in NO_COOKIE_AUTH:
        return NO_COOKIE_AUTH[path]
    for prefix, reason in NO_COOKIE_AUTH_PREFIX.items():
        if path.startswith(prefix):
            return reason
    return None


def violations(rows: list[dict]) -> list[str]:
    out = []
    for row in rows:
        key = (row["method"], row["path"])
        if row["kind"] == "none":
            if no_cookie_reason(row["path"]) is None:
                out.append(f"{row['method']} {row['path']}: нет ни сессии, ни известного альтернативного auth")
            continue
        if row["method"] not in MUTATING:
            continue
        if row["kind"] == "session":
            if key not in CURRENT_USER_ONLY:
                out.append(f"{row['method']} {row['path']}: мутирует, но защищён только current_user — "
                           "добавь require_* или обоснование в CURRENT_USER_ONLY")
            continue
        assert row["roles"] is not None
        for ro in READ_ONLY_ROLES:
            if ro in row["roles"]:
                out.append(f"{row['method']} {row['path']}: read-only роль {ro} допущена к записи")
        if "finance" in row["roles"] and not row["path"].startswith("/billing"):
            out.append(f"{row['method']} {row['path']}: finance допущена к записи вне /billing")
    for key in CURRENT_USER_ONLY:
        if not any((r["method"], r["path"]) == key for r in rows):
            out.append(f"{key[0]} {key[1]}: есть в CURRENT_USER_ONLY, но маршрута больше нет — убери запись")
    return out


def render(rows: list[dict]) -> str:
    lines = [
        "# Матрица RBAC по маршрутам",
        "",
        "Сгенерировано `python backend/scripts/rbac_matrix.py --write` из объектов FastAPI;",
        "`tests/test_rbac_matrix.py` падает, если файл устарел или нарушены инварианты",
        "(read-only роли `viewer`/`analyst` не пишут; `finance` пишет только в `/billing`;",
        "каждый мутирующий маршрут либо под `require_*`, либо явно обоснован).",
        "",
        "Роли: " + ", ".join(rbac.ALL_ROLES) + ".",
        "",
        "| Метод | Путь | Guard | Кто проходит |",
        "|---|---|---|---|",
    ]
    for row in rows:
        if row["kind"] == "none":
            who = no_cookie_reason(row["path"]) or "**НЕ ЗАЩИЩЁН**"
        elif row["kind"] == "session":
            note = CURRENT_USER_ONLY.get((row["method"], row["path"]))
            who = row["desc"] + (f" — {note}" if note else "")
        else:
            who = row["desc"]
        mark = "**" if row["method"] in MUTATING else ""
        lines.append(f"| {mark}{row['method']}{mark} | `{row['path']}` | {row['kind']} | {who} |")
    lines += [
        "",
        "Чтение (`GET`) под `session` изолируется per-query через `visible_user_ids`/`WHERE user_id` —",
        "покрыто `tests/test_idor_isolation_db.py`.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    import main as app_module

    rows = build_rows(app_module.app)
    text = render(rows)
    if "--write" in argv:
        DOC_PATH.write_text(text, encoding="utf-8")
        print(f"written {DOC_PATH} ({len(rows)} routes)")
        return 0
    if "--check" in argv:
        current = DOC_PATH.read_text(encoding="utf-8") if DOC_PATH.exists() else ""
        if current != text:
            print("docs/audit/rbac_matrix.md устарел — запусти scripts/rbac_matrix.py --write")
            return 1
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
