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

ALL_ROLES = ("superadmin", "admin", "manager", "advertiser", "analyst", "finance")
ADMIN_ROLES = ("superadmin", "admin")
# кто может создавать/менять кампании, креативы, line items и т.п.
CAMPAIGN_WRITE = ("superadmin", "admin", "manager", "advertiser")
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
