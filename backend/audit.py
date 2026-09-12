"""Append-only audit log: middleware пишет все мутации (POST/PUT/PATCH/DELETE).

Не трогает горячий RTB-путь (/rtb/*, /px/*) и health.
Пишется best-effort: сбой записи аудита не валит запрос (логируем в stderr).
"""
import json
import sys

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from db import get_pool

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# Публичные высокочастотные POST (win/imp/click биддера, пиксель) и служебные
# пути не пишутся в audit_log — иначе одна строка на каждое событие трафика.
# Управляющие ручки пикселя (/pixels...) под префикс не попадают и аудируются.
SKIP_PREFIXES = ("/rtb/", "/pixel/collect", "/health", "/ready", "/auth/login", "/auth/register", "/cron/")


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        try:
            if request.method in MUTATING and not request.url.path.startswith(SKIP_PREFIXES):
                await self._log(request, response.status_code)
        except Exception as exc:  # noqa: BLE001 — аудит не должен ронять запрос
            print(f"[audit] write failed: {exc}", file=sys.stderr)
        return response

    async def _log(self, request: Request, status_code: int) -> None:
        user_id = user_email = user_role = None
        token = request.cookies.get("cdsp_session")
        pool = await get_pool()
        if token:
            row = await pool.fetchrow(
                """
                SELECT u.id, u.email, u.role
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token = $1 AND s.expires_at > now()
                """,
                token,
            )
            if row:
                user_id, user_email, user_role = row["id"], row["email"], row["role"]
        parts = [p for p in request.url.path.strip("/").split("/") if p]
        entity = parts[0] if parts else None
        entity_id = parts[1] if len(parts) > 1 else None
        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else None)
        if ip and "," in ip:
            ip = ip.split(",")[0].strip()
        await pool.execute(
            """
            INSERT INTO audit_log (user_id, user_email, user_role, method, path,
                                   status_code, entity, entity_id, ip)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            user_id, user_email, user_role, request.method, request.url.path,
            status_code, entity, entity_id, ip,
        )
