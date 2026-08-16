"""Session-based auth: bcrypt password hashing + opaque session tokens."""
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Cookie, Depends, HTTPException

from db import get_pool

SESSION_TTL_DAYS = 30
COOKIE_NAME = "cdsp_session"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


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
        SELECT u.id, u.email, u.name, u.role, u.status, s.token, s.expires_at
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
    return dict(row)


def require_role(*roles: str):
    async def checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user
    return checker
