"""Audit F-02 / B14b: наценка на share-ссылках и гостях — snapshot и права.

Договор:
  snapshot = chain_mult(создатель) × (1 + extra/100); extra > 0 — только
  rbac.MARKUP_ADD_ROLES (superadmin/admin/agency); manager/advertiser/client/viewer
  передают ровно свою цену (extra=0, mode=inherited). Получатель (public по токену
  или viewer через stats._resolve_scope) видит net × snapshot, без пересчёта
  по текущим users.markup_pct. Отозванный гость — 403.
"""
from __future__ import annotations

import asyncio
import re
from datetime import UTC
from pathlib import Path

import pytest
from fastapi import HTTPException

import rbac
from routers import share, stats


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture
def chain_pct(monkeypatch):
    """effective_markup_pct → фиксированные 20 % для любого пользователя."""
    async def fake(_uid):
        return 20.0
    monkeypatch.setattr(rbac, "effective_markup_pct", fake)
    return 20.0


# --- rbac -----------------------------------------------------------------------------

def test_markup_role_sets_are_disjoint_from_end_receivers():
    assert set(rbac.MARKUP_ADD_ROLES) == {"superadmin", "admin", "agency"}
    assert set(rbac.MARKUP_VIEW_ROLES) == {"agency", "client"}
    for r in ("manager", "advertiser", "client", "viewer", "analyst", "finance"):
        assert r not in rbac.MARKUP_ADD_ROLES


def test_spend_multiplier_only_for_view_roles(chain_pct):
    assert _run(rbac.spend_multiplier("u", "agency")) == pytest.approx(1.2)
    assert _run(rbac.spend_multiplier("u", "client")) == pytest.approx(1.2)
    for r in ("superadmin", "admin", "manager", "advertiser", "viewer"):
        assert _run(rbac.spend_multiplier("u", r)) == 1.0


def test_share_multiplier_is_multiplicative():
    assert rbac.share_multiplier(1.2, 10) == pytest.approx(1.32)
    assert rbac.share_multiplier(1.0, 0) == 1.0
    assert rbac.share_multiplier(1.0, None) == 1.0
    assert rbac.share_multiplier(1.32, 15) == pytest.approx(1.518)


# --- share._markup_snapshot ------------------------------------------------------------

def test_agency_can_add_extra_on_top_of_own_price(chain_pct):
    snap = _run(share._markup_snapshot({"id": "a", "role": "agency"}, 10))
    assert snap.extra_pct == 10
    assert snap.mult == pytest.approx(1.32)
    assert snap.mode == share.MARKUP_MODE_CUSTOM


def test_admin_adds_extra_over_net(chain_pct):
    snap = _run(share._markup_snapshot({"id": "a", "role": "admin"}, 25))
    assert snap.mult == pytest.approx(1.25)
    assert snap.mode == share.MARKUP_MODE_CUSTOM


@pytest.mark.parametrize("role", ["manager", "advertiser", "client", "viewer"])
def test_end_receiver_roles_cannot_add_extra(chain_pct, role):
    with pytest.raises(HTTPException) as ei:
        _run(share._markup_snapshot({"id": "u", "role": role}, 5))
    assert ei.value.status_code == 422


@pytest.mark.parametrize("extra", [None, 0, 0.0])
def test_default_is_creators_own_price(chain_pct, extra):
    client = _run(share._markup_snapshot({"id": "c", "role": "client"}, extra))
    assert client.mult == pytest.approx(1.2)
    assert client.mode == share.MARKUP_MODE_INHERITED
    manager = _run(share._markup_snapshot({"id": "m", "role": "manager"}, extra))
    assert manager.mult == 1.0
    assert manager.mode == share.MARKUP_MODE_INHERITED


def test_schema_bounds_on_extra_pct():
    from pydantic import ValidationError
    share.ShareLinkIn(name="n", markup_extra_pct=500)
    with pytest.raises(ValidationError):
        share.ShareLinkIn(name="n", markup_extra_pct=-1)
    with pytest.raises(ValidationError):
        share.GuestIn(email="a@b.co", password="password123", name="n", markup_extra_pct=501)


# --- serialization ---------------------------------------------------------------------

def test_serialize_link_exposes_snapshot_and_revocation():
    from datetime import datetime
    from decimal import Decimal
    now = datetime(2026, 9, 10, tzinfo=UTC)
    row = {
        "id": "00000000-0000-0000-0000-000000000001", "advertiser_id": None, "token": "t", "name": "n",
        "brand_name": None, "brand_color": None, "brand_logo_url": None, "expires_at": None,
        "revoked": False, "revoked_at": now, "revoked_by_user_id": "00000000-0000-0000-0000-000000000009",
        "created_by_user_id": None, "created_at": now,
        "markup_extra_pct": Decimal("10.000"), "markup_mult_snapshot": Decimal("1.320000"),
        "markup_mode": "custom_for_share",
    }
    out = share._serialize_link(row)
    assert out["revoked"] is True  # revoked_at — источник истины
    assert out["revoked_by_user_id"].endswith("9")
    assert out["markup_extra_pct"] == 10.0
    assert out["markup_mult_snapshot"] == pytest.approx(1.32)
    assert out["effective_markup_pct"] == pytest.approx(32.0)
    assert out["markup_mode"] == "custom_for_share"


# --- stats._resolve_scope для viewer ----------------------------------------------------

class _ViewerPool:
    def __init__(self, row):
        self.row = row

    async def fetchrow(self, *_):
        return self.row


def test_viewer_scope_uses_snapshot(monkeypatch):
    from decimal import Decimal
    async def pool():
        return _ViewerPool({"owner_id": "o", "advertiser_id": None,
                            "markup_mult_snapshot": Decimal("1.320000"), "revoked_at": None})
    monkeypatch.setattr(stats, "get_pool", pool)
    uids, adv, mult = _run(stats._resolve_scope({"id": "g", "role": "viewer"}))
    assert uids == ["o"] and adv is None
    assert mult == pytest.approx(1.32)


def test_viewer_scope_403_when_revoked(monkeypatch):
    from datetime import datetime
    async def pool():
        return _ViewerPool({"owner_id": "o", "advertiser_id": None, "markup_mult_snapshot": 1,
                            "revoked_at": datetime.now(UTC)})
    monkeypatch.setattr(stats, "get_pool", pool)
    with pytest.raises(HTTPException) as ei:
        _run(stats._resolve_scope({"id": "g", "role": "viewer"}))
    assert ei.value.status_code == 403


def test_non_viewer_scope_delegates_to_spend_multiplier(monkeypatch, chain_pct):
    async def vis(_u):
        return None
    monkeypatch.setattr(stats, "visible_user_ids", vis)
    _, _, m_client = _run(stats._resolve_scope({"id": "c", "role": "client"}))
    _, _, m_admin = _run(stats._resolve_scope({"id": "a", "role": "admin"}))
    assert m_client == pytest.approx(1.2) and m_admin == 1.0


# --- public_stats: цена из snapshot, не из текущего markup ------------------------------

def test_public_stats_uses_link_snapshot_not_live_markup(monkeypatch):
    from decimal import Decimal

    class _Pool:
        async def fetchrow(self, sql, *a):
            return {"bids": 0, "wins": 0, "impressions": 0, "clicks": 0, "spend": Decimal(100)}

        async def fetch(self, sql, *a):
            return []

    link = {"user_id": "o", "advertiser_id": None, "brand_name": None, "brand_color": None,
            "brand_logo_url": None, "name": "r", "markup_mult_snapshot": Decimal("1.5")}

    async def resolve(_t):
        return link

    async def pool():
        return _Pool()

    async def boom(_uid):  # текущая цепочка не должна опрашиваться
        raise AssertionError("effective_markup_pct must not be called for public stats")

    monkeypatch.setattr(share, "_resolve_link", resolve)
    monkeypatch.setattr(share, "get_pool", pool)
    monkeypatch.setattr(rbac, "effective_markup_pct", boom)
    out = _run(share.public_stats("tok"))
    assert out["summary"]["spend"] == pytest.approx(150.0)


# --- migration 044: контракт с кодом -------------------------------------------------

MIG = Path(__file__).resolve().parents[1] / "migrations" / "044_share_markup_snapshot.sql"


def test_migration_044_shape():
    sql = MIG.read_text()
    for tbl in ("share_links", "guest_access"):
        for col in ("created_by_user_id", "markup_extra_pct", "markup_mult_snapshot",
                    "markup_mode", "revoked_at", "revoked_by_user_id"):
            assert re.search(rf"ADD COLUMN IF NOT EXISTS\s+{col}", sql), (tbl, col)
        assert f"{tbl}_markup_mode_chk" in sql
    assert "'inherited', 'custom_for_share'" in sql
    # backfill зеркалит rbac.MARKUP_VIEW_ROLES — при смене ролей менять оба места
    roles_sql = ", ".join(f"'{r}'" for r in rbac.MARKUP_VIEW_ROLES)
    assert sql.count(f"u.role IN ({roles_sql})") == 2
    # идемпотентность: backfill только для строк без created_by
    assert sql.count("created_by_user_id IS NULL") == 2


def test_users_router_block_revokes_access(monkeypatch):
    """patch_user(status=blocked) → revoke_all_access_of(target, admin)."""
    import routers.users_router as ur

    calls = {}

    class _Pool:
        async def fetchrow(self, sql, *a):
            if sql.startswith("SELECT id, role"):
                return {"id": a[0], "role": "agency"}
            return {"id": a[0], "email": "e", "name": "n", "role": "agency", "status": "blocked",
                    "parent_user_id": None, "markup_pct": 0, "permissions": {}, "created_at": None}

    async def pool():
        return _Pool()

    async def rs(uid):
        calls["sessions"] = uid

    async def ra(owner, by):
        calls["access"] = (owner, by)

    monkeypatch.setattr(ur, "get_pool", pool)
    monkeypatch.setattr(ur, "revoke_sessions", rs)
    monkeypatch.setattr(share, "revoke_all_access_of", ra)
    monkeypatch.setattr(ur, "_user_out", lambda row: dict(row))
    _run(ur.patch_user("target", ur.UserPatch(status="blocked"), user={"id": "admin1", "role": "admin"}))
    assert calls == {"sessions": "target", "access": ("target", "admin1")}
