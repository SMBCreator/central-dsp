"""Session-based auth: bcrypt password hashing + opaque session tokens."""
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Cookie, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from db import get_pool

SESSION_TTL_DAYS = 30
COOKIE_NAME = "cdsp_session"

# bcrypt работает с первыми 72 байтами: старые версии молча обрезали хвост,
# bcrypt>=4.1 бросает ValueError (=> 500 в register). Лимит проверяется в
# схемах (422 с понятным текстом) и здесь — как последняя линия (audit S-04).
PASSWORD_MAX_BYTES = 72
PASSWORD_TOO_LONG = f"Пароль длиннее {PASSWORD_MAX_BYTES} байт в UTF-8 — bcrypt не примет"


def password_bytes_ok(password: str) -> bool:
    return len(password.encode("utf-8")) <= PASSWORD_MAX_BYTES


def hash_password(password: str) -> str:
    if not password_bytes_ok(password):
        raise ValueError(PASSWORD_TOO_LONG)
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    if not password_bytes_ok(password):
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


async def hash_password_async(password: str) -> str:
    """bcrypt ~100–300 мс CPU: в event loop это блокирует все RTB/UI-запросы."""
    return await run_in_threadpool(hash_password, password)


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await run_in_threadpool(verify_password, password, password_hash)


async def revoke_sessions(user_id: str, *, keep_token: str | None = None) -> int:
    """Отзывает сессии пользователя (смена/сброс пароля, блокировка).
    keep_token — текущая сессия, которую оставляем (сам сменил пароль)."""
    pool = await get_pool()
    if keep_token:
        res = await pool.execute(
            "DELETE FROM sessions WHERE user_id = $1 AND token <> $2", user_id, keep_token
        )
    else:
        res = await pool.execute("DELETE FROM sessions WHERE user_id = $1", user_id)
    try:
        return int(res.split()[-1])
    except (ValueError, IndexError):
        return 0


async def create_session(user_id: str) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO sessions (user_id, token, expires_at) VALUES ($1, $2, $3)",
        user_id, token, expires_at,
    )
    return token, expires_at


async def destroy_session(token: str) -> None:
    pool = await get_pool()
    await pool.execute("DELETE FROM sessions WHERE token = $1", token)


async def current_user(cdsp_session: str | None = Cookie(default=None)) -> dict:
    if not cdsp_session:
        raise HTTPException(status_code=401, detail="Не авторизован")
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT u.id, u.email, u.name, u.role, u.status, u.parent_user_id, u.markup_pct, u.permissions, s.token, s.expires_at
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token = $1
        """,
        cdsp_session,
    )
    if not row:
        raise HTTPException(status_code=401, detail="Сессия недействительна")
    if row["expires_at"] < datetime.now(timezone.utc):
        await destroy_session(cdsp_session)
        raise HTTPException(status_code=401, detail="Сессия истекла")
    if row["status"] != "active":
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован")
    u = dict(row)
    perms = u.get("permissions")
    if isinstance(perms, str):
        import json as _json
        try:
            u["permissions"] = _json.loads(perms)
        except Exception:
            u["permissions"] = {}
    elif perms is None:
        u["permissions"] = {}
    return u


def require_role(*roles: str):
    async def checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user
    return checker
