import asyncpg
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response

from auth import (
    COOKIE_NAME,
    create_session,
    current_user,
    destroy_session,
    hash_password_async,
    revoke_sessions,
    verify_password_async,
)
from cache import clear_login_failures, login_blocked, record_login_failure
from csrf import cookie_samesite
from db import get_pool
from schemas import ChangePasswordIn, LoginIn, ProfilePatch, RegisterIn
from startup_checks import registration_mode

# Один ключ advisory-lock на bootstrap первого пользователя (S-04). Число
# произвольное, но фиксированное: другие advisory-lock'и в проекте его не используют.
BOOTSTRAP_LOCK_KEY = 0x0D5B0075

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    # SameSite: lax в production (второй слой против CSRF), none — только по
    # AUTH_COOKIE_SAMESITE=none для UI в чужом iframe (audit S-01).
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite=cookie_samesite(),
        max_age=30 * 86400,
        path="/",
    )


@router.post("/register")
async def register(body: RegisterIn, response: Response):
    pool = await get_pool()
    email = body.email.lower()
    existing = await pool.fetchval("SELECT 1 FROM users WHERE email = $1", email)
    if existing:
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")
    # bcrypt до транзакции: не держим lock/соединение на 200 мс CPU.
    password_hash = await hash_password_async(body.password)
    # Первый пользователь = superadmin (bootstrap). Дальше:
    # REGISTRATION_MODE=open   -> новые пользователи получают роль manager (dev)
    # REGISTRATION_MODE=closed -> самостоятельная регистрация запрещена (default, invite-only)
    # Bootstrap сериализован advisory-lock'ом в одной транзакции с count+INSERT:
    # два параллельных register на пустой БД не могут оба стать superadmin (S-04).
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", BOOTSTRAP_LOCK_KEY)
            count = await conn.fetchval("SELECT count(*) FROM users")
            if count == 0:
                role = "superadmin"
            elif registration_mode() == "closed":
                raise HTTPException(
                    status_code=403, detail="Регистрация закрыта. Запросите приглашение у администратора."
                )
            else:
                role = "manager"
            try:
                row = await conn.fetchrow(
                    "INSERT INTO users (email, password_hash, name, role) VALUES ($1, $2, $3, $4) "
                    "RETURNING id, email, name, role",
                    email, password_hash, body.name, role,
                )
            except asyncpg.UniqueViolationError:
                raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")
    token, _ = await create_session(str(row["id"]))
    _set_cookie(response, token)
    # Токен сессии живёт только в HttpOnly-cookie: в теле его нет, чтобы фронт
    # не мог (и не пытался) положить его в localStorage — XSS ≠ угон сессии.
    return {"id": str(row["id"]), "email": row["email"], "name": row["name"], "role": row["role"]}


def _client_ip(request: Request) -> str:
    """IP клиента за реверс-прокси (Caddy/nginx ставят X-Forwarded-For).
    Без заголовка — адрес соединения. Тот же приём, что в global_rate_limiter."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    # GAP-002 / D5: защита от перебора пароля. Считаем НЕУДАЧНЫЕ попытки в двух
    # окнах — по логину и по IP (cache.login_blocked). Успешные входы не
    # тратят лимит; успех сбрасывает счётчик по логину. Ответ 429 одинаков для
    # существующего и несуществующего логина, чтобы не раскрывать список users.
    client_ip = _client_ip(request)
    email_key = body.email.lower().strip()
    retry_after = await login_blocked(email_key, client_ip)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Слишком много неудачных попыток входа. Повторите позже.",
            headers={"Retry-After": str(retry_after)},
        )
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT id, email, name, role, status, password_hash FROM users WHERE email = $1",
        body.email.lower(),
    )
    if not row or not await verify_password_async(body.password, row["password_hash"]):
        await record_login_failure(email_key, client_ip)
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    if row["status"] != "active":
        # Пароль верный, но аккаунт заблокирован — это не перебор, счётчик не трогаем.
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован")
    await clear_login_failures(email_key)
    token, _ = await create_session(str(row["id"]))
    _set_cookie(response, token)
    return {"id": str(row["id"]), "email": row["email"], "name": row["name"], "role": row["role"]}


@router.post("/logout")
async def logout(response: Response, cdsp_session: str | None = Cookie(default=None)):
    if cdsp_session:
        await destroy_session(cdsp_session)
    # Атрибуты должны совпадать с set_cookie, иначе браузер не сопоставит cookie.
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite=cookie_samesite())
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    from rbac import PERMISSION_FLAGS, has_permission

    return {
        "id": str(user["id"]),
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        # эффективные права (для client — по флагам из БД, для admin-ролей — True)
        "permissions": {flag: has_permission(user, flag) for flag in PERMISSION_FLAGS},
    }


@router.patch("/me")
async def update_me(body: ProfilePatch, user: dict = Depends(current_user)):
    """H9: обновление имени в профиле."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "UPDATE users SET name = $2 WHERE id = $1 RETURNING id, email, name, role",
        user["id"], body.name.strip(),
    )
    return {"id": str(row["id"]), "email": row["email"], "name": row["name"], "role": row["role"]}


@router.post("/change-password")
async def change_password(body: ChangePasswordIn, user: dict = Depends(current_user)):
    """H9: смена пароля с проверкой текущего. S-04: остальные сессии отзываются —
    смена пароля обычно значит «кто-то ещё мог знать старый»."""
    pool = await get_pool()
    row = await pool.fetchrow("SELECT password_hash FROM users WHERE id = $1", user["id"])
    if not row or not await verify_password_async(body.current_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="Текущий пароль указан неверно")
    await pool.execute(
        "UPDATE users SET password_hash = $2 WHERE id = $1",
        user["id"], await hash_password_async(body.new_password),
    )
    revoked = await revoke_sessions(str(user["id"]), keep_token=user.get("token"))
    return {"ok": True, "revoked_sessions": revoked}
