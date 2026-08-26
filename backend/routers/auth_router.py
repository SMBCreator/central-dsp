import os

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response

from auth import (
    COOKIE_NAME,
    create_session,
    current_user,
    destroy_session,
    hash_password,
    verify_password,
)
from cache import rate_limit
from db import get_pool
from schemas import LoginIn, RegisterIn

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=30 * 86400,
        path="/",
    )


@router.post("/register")
async def register(body: RegisterIn, response: Response):
    pool = await get_pool()
    existing = await pool.fetchval("SELECT 1 FROM users WHERE email = $1", body.email.lower())
    if existing:
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")
    count = await pool.fetchval("SELECT count(*) FROM users")
    # Первый пользователь = superadmin (bootstrap). Дальше:
    # REGISTRATION_MODE=open  -> новые пользователи получают роль manager (dev)
    # REGISTRATION_MODE=closed -> самостоятельная регистрация запрещена (прод, invite-only)
    if count == 0:
        role = "superadmin"
    elif os.environ.get("REGISTRATION_MODE", "open") == "closed":
        raise HTTPException(status_code=403, detail="Регистрация закрыта. Запросите приглашение у администратора.")
    else:
        role = "manager"
    row = await pool.fetchrow(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1, $2, $3, $4) RETURNING id, email, name, role",
        body.email.lower(), hash_password(body.password), body.name, role,
    )
    token, _ = await create_session(str(row["id"]))
    _set_cookie(response, token)
    return {"id": str(row["id"]), "email": row["email"], "name": row["name"], "role": row["role"], "token": token}


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    # GAP-002: brute-force protection. Two fixed windows: per-IP and per-email.
    client_ip = request.client.host if request.client else "unknown"
    email_key = body.email.lower().strip()
    ip_ok = await rate_limit("login:ip", client_ip, limit=20, window_seconds=300)
    email_ok = await rate_limit("login:email", email_key, limit=8, window_seconds=300)
    if not ip_ok or not email_ok:
        raise HTTPException(
            status_code=429,
            detail="Слишком много попыток входа. Повторите через несколько минут.",
        )
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT id, email, name, role, status, password_hash FROM users WHERE email = $1",
        body.email.lower(),
    )
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    if row["status"] != "active":
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован")
    token, _ = await create_session(str(row["id"]))
    _set_cookie(response, token)
    return {"id": str(row["id"]), "email": row["email"], "name": row["name"], "role": row["role"], "token": token}


@router.post("/logout")
async def logout(response: Response, cdsp_session: str | None = Cookie(default=None)):
    if cdsp_session:
        await destroy_session(cdsp_session)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    return {"id": str(user["id"]), "email": user["email"], "name": user["name"], "role": user["role"]}
