"""RBAC: матрица ролей и tenant scoping (unit, в стиле остальных тестов — asyncio.run)."""
import asyncio

import pytest
from fastapi import HTTPException

import rbac


def _user(role: str) -> dict:
    return {"id": "u1", "email": "t@t", "role": role, "status": "active"}


def _check(checker, role: str) -> bool:
    """True — доступ разрешён, False — 403."""
    try:
        asyncio.run(checker(_user(role)))
        return True
    except HTTPException as e:
        assert e.status_code == 403
        return False


@pytest.mark.parametrize("role,allowed", [
    ("superadmin", True), ("admin", True), ("manager", True),
    ("advertiser", True), ("analyst", False), ("finance", False),
])
def test_campaign_write_matrix(role, allowed):
    checker = rbac.require_roles(*rbac.CAMPAIGN_WRITE)
    assert _check(checker, role) is allowed


@pytest.mark.parametrize("role,allowed", [
    ("superadmin", True), ("admin", True), ("finance", True),
    ("manager", False), ("advertiser", False), ("analyst", False),
])
def test_finance_write_matrix(role, allowed):
    checker = rbac.require_roles(*rbac.FINANCE_WRITE)
    assert _check(checker, role) is allowed


@pytest.mark.parametrize("role,allowed", [
    ("superadmin", True), ("admin", True), ("manager", True),
    ("advertiser", False), ("analyst", False), ("finance", False),
])
def test_platform_write_matrix(role, allowed):
    checker = rbac.require_roles(*rbac.PLATFORM_WRITE)
    assert _check(checker, role) is allowed


def test_can_see_all():
    for role in ("superadmin", "admin", "analyst", "finance"):
        assert rbac.can_see_all(_user(role)), role
    for role in ("manager", "advertiser", "agency", "client"):
        assert not rbac.can_see_all(_user(role)), role


def test_all_roles_covered():
    assert set(rbac.ALL_ROLES) == {
        "superadmin", "admin", "manager", "advertiser", "analyst", "finance",
        "agency", "client",
    }


def test_new_roles_scoping():
    # agency/client не видят всё
    for role in ("agency", "client"):
        assert not rbac.can_see_all(_user(role)), role
    # agency входит в CAMPAIGN_WRITE, client — нет (только с флагом)
    assert "agency" in rbac.CAMPAIGN_WRITE
    assert "client" not in rbac.CAMPAIGN_WRITE


def test_client_can_edit_flag():
    u = _user("client")
    u["permissions"] = {}
    assert not rbac.client_can_edit(u)
    u["permissions"] = {"can_edit_campaigns": True}
    assert rbac.client_can_edit(u)
    # для остальных ролей флаг не нужен
    assert rbac.client_can_edit(_user("manager"))
