"""Пользователи и доступы: иерархия superadmin -> manager/agency -> client.

- superadmin/admin: создают любых пользователей, задают наценку и ограничения
- agency: создаёт только своих клиентов (parent = agency), задаёт им наценку/права
- client: read-only, самостоятельный запуск включается флагом can_edit_campaigns
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from auth import current_user, hash_password
from db import get_pool
from rbac import ADMIN_ROLES, effective_markup_pct, require_roles, visible_user_ids

# Управлять пользователями могут admin-роли и agency (только своими client);
# грубый фильтр — здесь, чтобы был виден в матрице RBAC, тонкий — в хендлерах.
require_user_manager = require_roles(*ADMIN_ROLES, "agency")

router = APIRouter(prefix="/users", tags=["users"])

CREATABLE_BY_ADMIN = ("admin", "manager", "advertiser", "analyst", "finance", "agency", "client", "viewer")
# 4 пофлаговых права (роль client). can_manage_billing — легаси-ключ, читается как can_view_billing.
ALLOWED_PERMISSIONS = ("can_edit_campaigns", "can_manage_creatives", "can_view_billing", "can_share_stats")
_LEGACY_PERMISSION_ALIASES = {"can_manage_billing": "can_view_billing"}


def _user_out(row: dict) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    if d.get("parent_user_id"):
        d["parent_user_id"] = str(d["parent_user_id"])
    d.pop("password_hash", None)
    perms = d.get("permissions")
    if isinstance(perms, str):
        try:
            perms = json.loads(perms)
        except Exception:
            perms = {}
    d["permissions"] = _clean_permissions(perms or {})
    d["markup_pct"] = float(d.get("markup_pct") or 0)
    return d


class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=128)
    role: str
    parent_user_id: str | None = None
    markup_pct: float = Field(default=0, ge=0, le=500)
    permissions: dict = Field(default_factory=dict)


class UserPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = None  # active | blocked
    markup_pct: float | None = Field(default=None, ge=0, le=500)
    permissions: dict | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


def _clean_permissions(perms: dict) -> dict:
    """Оставляем только известные флаги; легаси-ключи переименовываем в актуальные."""
    out: dict = {}
    for k, v in perms.items():
        key = _LEGACY_PERMISSION_ALIASES.get(k, k)
        if key in ALLOWED_PERMISSIONS:
            out[key] = out.get(key, False) or bool(v)
    return out


@router.get("")
async def list_users(user: dict = Depends(current_user)):
    if user["role"] not in (*ADMIN_ROLES, "agency"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    pool = await get_pool()
    scope = await visible_user_ids(user)
    if scope is None:
        rows = await pool.fetch(
            "SELECT id, email, name, role, status, parent_user_id, markup_pct, permissions, created_at "
            "FROM users ORDER BY created_at"
        )
    else:
        rows = await pool.fetch(
            "SELECT id, email, name, role, status, parent_user_id, markup_pct, permissions, created_at "
            "FROM users WHERE id = ANY($1::uuid[]) ORDER BY created_at",
            scope,
        )
    out = [_user_out(r) for r in rows]
    for u in out:
        u["effective_markup_pct"] = await effective_markup_pct(u["id"])
    return out


@router.post("", status_code=201)
async def create_user(body: UserCreate, user: dict = Depends(require_user_manager)):
    pool = await get_pool()
    if user["role"] in ADMIN_ROLES:
        if body.role not in CREATABLE_BY_ADMIN:
            raise HTTPException(status_code=400, detail="Недопустимая роль")
        parent_id = body.parent_user_id
        if parent_id:
            ok = await pool.fetchrow("SELECT 1 FROM users WHERE id = $1", parent_id)
            if not ok:
                raise HTTPException(status_code=404, detail="Родительский пользователь не найден")
    elif user["role"] == "agency":
        if body.role != "client":
            raise HTTPException(status_code=403, detail="Агентство может создавать только клиентов")
        parent_id = str(user["id"])  # клиент всегда под агентством
    else:
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    dup = await pool.fetchrow("SELECT 1 FROM users WHERE email = $1", body.email.lower())
    if dup:
        raise HTTPException(status_code=409, detail="Email уже зарегистрирован")

    row = await pool.fetchrow(
        """
        INSERT INTO users (email, password_hash, name, role, parent_user_id, markup_pct, permissions)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        RETURNING id, email, name, role, status, parent_user_id, markup_pct, permissions, created_at
        """,
        body.email.lower(), hash_password(body.password), body.name, body.role,
        parent_id, body.markup_pct, json.dumps(_clean_permissions(body.permissions)),
    )
    return _user_out(row)


@router.patch("/{user_id}")
async def patch_user(user_id: str, body: UserPatch, user: dict = Depends(require_user_manager)):
    pool = await get_pool()
    if user["role"] in ADMIN_ROLES:
        target = await pool.fetchrow("SELECT id, role FROM users WHERE id = $1", user_id)
    elif user["role"] == "agency":
        scope = await visible_user_ids(user) or []
        if user_id not in scope or user_id == str(user["id"]):
            raise HTTPException(status_code=403, detail="Можно менять только своих клиентов")
        target = await pool.fetchrow("SELECT id, role FROM users WHERE id = $1", user_id)
    else:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target["role"] == "superadmin" and user["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="Суперадмина меняет только суперадмин")

    sets, values = [], []
    if body.name is not None:
        sets.append(f"name = ${len(values) + 2}"); values.append(body.name)
    if body.status is not None:
        if body.status not in ("active", "blocked"):
            raise HTTPException(status_code=400, detail="Статус: active | blocked")
        sets.append(f"status = ${len(values) + 2}"); values.append(body.status)
    if body.markup_pct is not None:
        sets.append(f"markup_pct = ${len(values) + 2}"); values.append(body.markup_pct)
    if body.permissions is not None:
        sets.append(f"permissions = ${len(values) + 2}::jsonb")
        values.append(json.dumps(_clean_permissions(body.permissions)))
    if body.password is not None:
        sets.append(f"password_hash = ${len(values) + 2}"); values.append(hash_password(body.password))
    if not sets:
        raise HTTPException(status_code=400, detail="Нет изменений")

    row = await pool.fetchrow(
        f"UPDATE users SET {', '.join(sets)} WHERE id = $1 "
        "RETURNING id, email, name, role, status, parent_user_id, markup_pct, permissions, created_at",
        user_id, *values,
    )
    return _user_out(row)


@router.get("/me/markup")
async def my_markup(user: dict = Depends(current_user)):
    return {"markup_pct": float(user.get("markup_pct") or 0),
            "effective_markup_pct": await effective_markup_pct(str(user["id"]))}
