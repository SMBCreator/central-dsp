"""RBAC: матрица 6 ролей + tenant scoping.

Роли (ТЗ):
  superadmin — всё, включая управление пользователями и системными настройками
  admin      — всё в рамках платформы (без системных операций)
  manager    — управление кампаниями/креативами/SSP/ОРД (свои сущности)
  advertiser — только свои кампании/креативы/статистика (tenant isolation)
  analyst    — read-only повсюду
  finance    — read-only + биллинговые операции (deposit/adjust)
"""
from fastapi import Depends, HTTPException

from auth import current_user

ALL_ROLES = ("superadmin", "admin", "manager", "advertiser", "analyst", "finance", "agency", "client")
ADMIN_ROLES = ("superadmin", "admin")
# кто может создавать/менять кампании, креативы, line items и т.п.
CAMPAIGN_WRITE = ("superadmin", "admin", "manager", "advertiser", "agency")
# кто управляет SSP-партнёрами, правилами, ОРД-регистрациями
PLATFORM_WRITE = ("superadmin", "admin", "manager")
# кто проводит деньги
FINANCE_WRITE = ("superadmin", "admin", "finance")


def require_roles(*roles: str):
    async def checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user
    return checker


require_campaign_write = require_roles(*CAMPAIGN_WRITE)
require_platform_write = require_roles(*PLATFORM_WRITE)
require_finance_write = require_roles(*FINANCE_WRITE)
require_admin = require_roles(*ADMIN_ROLES)


def is_admin(user: dict) -> bool:
    return user["role"] in ADMIN_ROLES


def can_see_all(user: dict) -> bool:
    """admin/superadmin/analyst/finance видят все сущности; manager/advertiser — только свои."""
    return user["role"] in ("superadmin", "admin", "analyst", "finance")

# ---------- Иерархия (agency -> client), наценки, ограничения ----------

async def visible_user_ids(user: dict) -> list[str] | None:
    """None = видит всех. Для agency: свой id + все потомки. Для остальных не-админов: только свой."""
    if can_see_all(user):
        return None
    if user["role"] != "agency":
        return [str(user["id"])]
    from db import get_pool
    pool = await get_pool()
    rows = await pool.fetch(
        """
        WITH RECURSIVE tree AS (
            SELECT id FROM users WHERE id = $1
            UNION ALL
            SELECT u.id FROM users u JOIN tree t ON u.parent_user_id = t.id
        )
        SELECT id FROM tree
        """,
        user["id"],
    )
    return [str(r["id"]) for r in rows]


async def effective_markup_pct(user_id: str) -> float:
    """Суммарная наценка по цепочке от пользователя вверх до корня.
    Наценки мультипликативны: (1+a)(1+b)-1."""
    from db import get_pool
    pool = await get_pool()
    rows = await pool.fetch(
        """
        WITH RECURSIVE chain AS (
            SELECT id, parent_user_id, markup_pct FROM users WHERE id = $1
            UNION ALL
            SELECT u.id, u.parent_user_id, u.markup_pct FROM users u JOIN chain c ON u.id = c.parent_user_id
        )
        SELECT markup_pct FROM chain
        """,
        user_id,
    )
    mult = 1.0
    for r in rows:
        mult *= 1.0 + float(r["markup_pct"] or 0) / 100.0
    return round((mult - 1.0) * 100.0, 4)


def client_can_edit(user: dict) -> bool:
    """client по умолчанию read-only; self-serve включается флагом can_edit_campaigns."""
    if user["role"] != "client":
        return True
    return bool((user.get("permissions") or {}).get("can_edit_campaigns"))


def require_campaign_edit():
    """CAMPAIGN_WRITE-роли + client с флагом can_edit_campaigns."""
    async def checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] in CAMPAIGN_WRITE:
            return user
        if user["role"] == "client" and client_can_edit(user):
            return user
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return checker


require_campaign_editor = require_campaign_edit()
