"""Готовность к запуску: compute_diagnostics + гейт PATCH status=active.

Unit-тесты без Postgres: пул подменяется заглушкой, которая отвечает по
подстроке SQL. Проверяем инварианты, а не SQL.
"""
import asyncio
import os
import sys
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routers import campaigns as camp_router  # noqa: E402
from schemas import CampaignPatch  # noqa: E402


class FakePool:
    """Отвечает на запросы диагностики по подстрокам SQL."""

    def __init__(self, *, li_total=1, li_active=1, cr_total=1, cr_approved=1,
                 cr_pending=0, unmarked=0, targets_ru=True, blocked=0,
                 adv=None, camp=None):
        self.li_total, self.li_active = li_total, li_active
        self.cr_total, self.cr_approved, self.cr_pending = cr_total, cr_approved, cr_pending
        self.unmarked, self.targets_ru, self.blocked = unmarked, targets_ru, blocked
        self.adv = adv if adv is not None else {"name": "A", "status": "active", "balance": 1000}
        self.camp = camp
        self.updated = None

    async def fetchval(self, sql, *args):
        s = " ".join(sql.split())
        if "FROM line_items" in s and "bool_or" in s:
            return self.targets_ru
        if "FROM line_items" in s and "status = 'active'" in s:
            return self.li_active
        if "FROM line_items" in s:
            return self.li_total
        if "FROM creatives" in s and "erid IS NULL" in s:
            return self.unmarked
        if "FROM creatives" in s and "status = 'approved'" in s:
            return self.cr_approved
        if "FROM creatives" in s and "status = 'pending'" in s:
            return self.cr_pending
        if "FROM creatives" in s:
            return self.cr_total
        if "FROM optimizer_blocks" in s:
            return self.blocked
        raise AssertionError(f"unexpected fetchval: {s}")

    async def fetchrow(self, sql, *args):
        s = " ".join(sql.split())
        if "FROM advertisers" in s:
            return self.adv
        if s.startswith("SELECT * FROM campaigns"):
            return self.camp
        if s.startswith("UPDATE campaigns"):
            self.updated = args
            return {**self.camp, "status": "active"}
        raise AssertionError(f"unexpected fetchrow: {s}")


def _camp(**over):
    base = {
        "id": "c1", "advertiser_id": "a1", "user_id": "u1", "status": "draft",
        "name": "T", "start_date": None, "end_date": None,
        "daily_budget": 1000, "total_budget": 0, "spent_today": 0, "spent_total": 0,
        "frequency_caps": None, "group_id": None, "pixel_id": None, "goal_id": None,
        "scoring_profile_id": None,
    }
    base.update(over)
    return base


def _diag(pool, camp, **kw):
    return asyncio.run(camp_router.compute_diagnostics(pool, camp, **kw))


def test_ready_campaign_has_no_launch_blockers():
    d = _diag(FakePool(), _camp(), assume_active=True)
    assert d["ready_to_launch"] is True
    assert d["launch_blockers"] == []
    assert d["blocker_count"] == 0


def test_draft_status_is_not_a_launch_blocker_but_is_a_delivery_blocker():
    d = _diag(FakePool(), _camp(status="draft"))
    codes = [c["code"] for c in d["checks"] if not c["ok"]]
    assert codes == ["campaign_status"]
    assert d["eligible"] is False
    # но к запуску готова: единственный блокер — сам статус
    assert d["ready_to_launch"] is True
    assert d["launch_blocker_count"] == 0


def test_launch_blockers_collected():
    pool = FakePool(li_total=0, li_active=0, cr_total=0, cr_approved=0,
                    adv={"name": "A", "status": "active", "balance": 0})
    d = _diag(pool, _camp(end_date=date.today() - timedelta(days=1)), assume_active=True)
    codes = {c["code"] for c in d["launch_blockers"]}
    assert codes == {"schedule", "balance", "line_items", "creatives"}
    assert d["ready_to_launch"] is False
    assert "campaign_status" not in codes


def test_ord_marking_blocks_only_for_ru_traffic():
    ru = _diag(FakePool(unmarked=1, targets_ru=True), _camp(), assume_active=True)
    assert {c["code"] for c in ru["launch_blockers"]} == {"ord_marking"}
    non_ru = _diag(FakePool(unmarked=1, targets_ru=False), _camp(), assume_active=True)
    assert non_ru["ready_to_launch"] is True


def test_daily_budget_exhausted_is_warning_not_blocker():
    d = _diag(FakePool(), _camp(daily_budget=100, spent_today=100), assume_active=True)
    assert d["ready_to_launch"] is True
    assert d["warning_count"] == 1
    assert d["checks"][3]["code"] == "daily_budget" and d["checks"][3]["ok"] is False


def _patch_active(pool, monkeypatch, gate="on"):
    monkeypatch.setenv("LAUNCH_GATE", gate)
    monkeypatch.delenv("ORD_LAUNCH_GUARD", raising=False)

    async def _get_pool():
        return pool

    monkeypatch.setattr(camp_router, "get_pool", _get_pool)
    user = {"id": "u1", "role": "admin", "status": "active"}
    return asyncio.run(
        camp_router.update_campaign("c1", CampaignPatch(status="active"), user)
    )


def test_patch_active_rejected_with_409_and_blocker_list(monkeypatch):
    pool = FakePool(cr_total=0, cr_approved=0, camp=_camp())
    with pytest.raises(HTTPException) as ei:
        _patch_active(pool, monkeypatch)
    assert ei.value.status_code == 409
    detail = ei.value.detail
    assert isinstance(detail, dict)
    assert [b["code"] for b in detail["blockers"]] == ["creatives"]
    assert pool.updated is None, "UPDATE не должен выполняться при блокерах"


def test_patch_active_passes_when_ready(monkeypatch):
    pool = FakePool(camp=_camp())
    out = _patch_active(pool, monkeypatch)
    assert out["status"] == "active"
    assert pool.updated is not None


def test_patch_active_gate_can_be_disabled_in_dev(monkeypatch):
    pool = FakePool(cr_total=0, cr_approved=0, camp=_camp())
    out = _patch_active(pool, monkeypatch, gate="off")
    assert out["status"] == "active"


def test_patch_pause_is_not_gated(monkeypatch):
    pool = FakePool(cr_total=0, cr_approved=0, camp=_camp(status="active"))

    async def _get_pool():
        return pool

    monkeypatch.setattr(camp_router, "get_pool", _get_pool)

    async def fetchrow(sql, *args):
        return {**pool.camp, "status": "paused"}

    pool.fetchrow = fetchrow
    user = {"id": "u1", "role": "admin", "status": "active"}
    out = asyncio.run(camp_router.update_campaign("c1", CampaignPatch(status="paused"), user))
    assert out["status"] == "paused"
