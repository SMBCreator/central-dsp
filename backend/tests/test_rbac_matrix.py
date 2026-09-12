"""Матрица A1: пофлаговые permissions и новые гейты (share/creatives/billing/settings).

Проверяет соответствие серверного RBAC утверждённой матрице ролей:
  - has_permission: admin-подобные роли — всегда True; client — по флагу; остальные — False;
  - require_share_manager: share-ссылки/гости только для write-ролей и client c can_share_stats;
  - require_creative_manager: креативы — write-роли + client c can_manage_creatives;
  - require_billing_viewer: биллинг видят admin-роли, finance, analyst и client c can_view_billing;
  - require_settings_write: настройки аккаунта — write-роли + client c can_edit_campaigns;
  - легаси-ключ can_manage_billing читается как can_view_billing.
"""
import asyncio

import pytest
from fastapi import HTTPException

import rbac

WRITE_ROLES = ("superadmin", "admin", "manager", "advertiser", "agency")
READONLY_ROLES = ("analyst", "finance", "viewer")


def _user(role: str, perms: dict | None = None) -> dict:
    u = {"id": "u1", "email": "t@t", "role": role, "status": "active"}
    if perms is not None:
        u["permissions"] = perms
    return u


def _allowed(checker, user: dict) -> bool:
    try:
        asyncio.run(checker(user))
        return True
    except HTTPException as e:
        assert e.status_code == 403
        return False


# ---------- has_permission ----------

@pytest.mark.parametrize("flag", rbac.PERMISSION_FLAGS)
@pytest.mark.parametrize("role", WRITE_ROLES)
def test_write_roles_have_all_flags(role, flag):
    assert rbac.has_permission(_user(role), flag)


@pytest.mark.parametrize("flag", rbac.PERMISSION_FLAGS)
@pytest.mark.parametrize("role", READONLY_ROLES)
def test_readonly_roles_have_no_flags(role, flag):
    assert not rbac.has_permission(_user(role), flag)


@pytest.mark.parametrize("flag", rbac.PERMISSION_FLAGS)
def test_client_flag_off_by_default(flag):
    assert not rbac.has_permission(_user("client", {}), flag)
    assert not rbac.has_permission(_user("client"), flag)


@pytest.mark.parametrize("flag", rbac.PERMISSION_FLAGS)
def test_client_flag_on(flag):
    assert rbac.has_permission(_user("client", {flag: True}), flag)


def test_client_flags_independent():
    u = _user("client", {"can_share_stats": True})
    assert rbac.has_permission(u, "can_share_stats")
    assert not rbac.has_permission(u, "can_edit_campaigns")
    assert not rbac.has_permission(u, "can_manage_creatives")
    assert not rbac.has_permission(u, "can_view_billing")


def test_legacy_can_manage_billing_alias():
    u = _user("client", {"can_manage_billing": True})
    assert rbac.has_permission(u, "can_view_billing")


# ---------- require_share_manager ----------

@pytest.mark.parametrize("role,allowed", [
    ("superadmin", True), ("admin", True), ("manager", True),
    ("advertiser", True), ("agency", True),
    ("analyst", False), ("finance", False), ("viewer", False),
])
def test_share_manager_matrix(role, allowed):
    assert _allowed(rbac.require_share_manager, _user(role)) is allowed


def test_share_manager_client_flag():
    assert not _allowed(rbac.require_share_manager, _user("client", {}))
    assert _allowed(rbac.require_share_manager, _user("client", {"can_share_stats": True}))


# ---------- require_creative_manager ----------

@pytest.mark.parametrize("role,allowed", [
    ("superadmin", True), ("admin", True), ("manager", True),
    ("advertiser", True), ("agency", True),
    ("analyst", False), ("finance", False), ("viewer", False),
])
def test_creative_manager_matrix(role, allowed):
    assert _allowed(rbac.require_creative_manager, _user(role)) is allowed


def test_creative_manager_client_flag():
    assert not _allowed(rbac.require_creative_manager, _user("client", {}))
    assert _allowed(rbac.require_creative_manager, _user("client", {"can_manage_creatives": True}))
    # can_edit_campaigns не даёт права на креативы
    assert not _allowed(rbac.require_creative_manager, _user("client", {"can_edit_campaigns": True}))


# ---------- require_billing_viewer ----------

@pytest.mark.parametrize("role,allowed", [
    ("superadmin", True), ("admin", True), ("manager", True),
    ("advertiser", True), ("agency", True),
    ("analyst", True), ("finance", True),   # аналитика денег — read-only просмотр
    ("viewer", False),
])
def test_billing_viewer_matrix(role, allowed):
    assert _allowed(rbac.require_billing_viewer, _user(role)) is allowed


def test_billing_viewer_client_flag():
    assert not _allowed(rbac.require_billing_viewer, _user("client", {}))
    assert _allowed(rbac.require_billing_viewer, _user("client", {"can_view_billing": True}))


# ---------- require_settings_write ----------

@pytest.mark.parametrize("role,allowed", [
    ("superadmin", True), ("admin", True), ("manager", True),
    ("advertiser", True), ("agency", True),
    ("analyst", False), ("finance", False), ("viewer", False),
])
def test_settings_write_matrix(role, allowed):
    assert _allowed(rbac.require_settings_write, _user(role)) is allowed


def test_settings_write_client_flag():
    assert not _allowed(rbac.require_settings_write, _user("client", {}))
    assert _allowed(rbac.require_settings_write, _user("client", {"can_edit_campaigns": True}))


# ---------- viewer в ALL_ROLES и вне write-списков ----------

def test_viewer_registered_and_readonly():
    assert "viewer" in rbac.ALL_ROLES
    assert "viewer" not in rbac.CAMPAIGN_WRITE
    assert "viewer" not in rbac.PLATFORM_WRITE
    assert "viewer" not in rbac.FINANCE_WRITE
    assert not rbac.can_see_all(_user("viewer"))
