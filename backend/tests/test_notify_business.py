"""C4b / N-03 (unit): валидация адресов, разрешение адресатов
campaign -> advertiser -> owner с учётом notify_inherit, дедуп-гейт emit
(in-app + письмо одинаково гейтятся), autorule_fired, run_checks изолирует
ошибки; контракт cron/scheduler/metrics; schemas принимают notify_emails."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notify_business as nb  # noqa: E402
import obs_metrics  # noqa: E402
import schemas  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    obs_metrics.reset_for_tests()
    yield
    obs_metrics.reset_for_tests()


def test_normalise_emails():
    assert nb.normalise_emails([" A@X.io ", "a@x.io", "", "b@y.org"]) == ["a@x.io", "b@y.org"]
    assert nb.normalise_emails(None) == []
    with pytest.raises(ValueError):
        nb.normalise_emails(["nope"])
    with pytest.raises(ValueError):
        nb.normalise_emails([f"u{i}@x.io" for i in range(21)])


def test_schemas_accept_and_normalise_notify_fields():
    assert schemas.CampaignPatch(notify_emails=["A@x.io", "a@x.io"], notify_inherit=False).model_dump(exclude_none=True) == {
        "notify_emails": ["a@x.io"], "notify_inherit": False}
    assert schemas.AdvertiserPatch(notify_emails=[]).notify_emails == []
    with pytest.raises(Exception):
        schemas.AdvertiserPatch(notify_emails=["bad"])


class _Pool:
    """Фейковый пул: campaigns/advertisers/users как словари, лог/notifications/outbox — списки."""

    def __init__(self, camp=None, adv=None, user_email="owner@x.io", user_status="active"):
        self.camp, self.adv, self.user_email, self.user_status = camp, adv, user_email, user_status
        self.log: set[str] = set()
        self.notifications: list[tuple] = []
        self.outbox: list[tuple] = []

    async def fetchrow(self, sql, *a):
        if "FROM campaigns" in sql and "notify_emails" in sql:
            return self.camp
        if "FROM campaigns" in sql and "name, user_id" in sql:
            return self.camp and {"name": self.camp.get("name", "C"), "user_id": self.camp["user_id"]}
        if "FROM advertisers" in sql:
            return self.adv
        if "INSERT INTO business_alert_log" in sql:
            if a[0] in self.log:
                return None
            self.log.add(a[0])
            return {"dedup_key": a[0]}
        if "INSERT INTO notification_outbox" in sql:
            self.outbox.append(a)
            return {"id": len(self.outbox)}
        raise AssertionError(sql)

    async def fetchval(self, sql, *a):
        assert "FROM users" in sql
        return self.user_email if self.user_status == "active" else None

    async def execute(self, sql, *a):
        assert "INSERT INTO notifications" in sql
        self.notifications.append(a)


def _camp(emails=(), inherit=True):
    return {"advertiser_id": "adv1", "user_id": "u1", "notify_emails": list(emails), "notify_inherit": inherit, "name": "Camp"}


def _adv(emails=()):
    return {"user_id": "u1", "notify_emails": list(emails)}


@pytest.mark.parametrize("camp,adv,expected", [
    (_camp(["c@x.io"]), _adv(["a@x.io"]), (["c@x.io"], "campaign")),
    (_camp(), _adv(["a@x.io"]), (["a@x.io"], "advertiser")),
    (_camp(), _adv(), (["owner@x.io"], "owner")),
    (_camp(inherit=False), _adv(["a@x.io"]), ([], "none")),
    (None, _adv(), ([], "none")),
])
def test_resolve_recipients_chain(camp, adv, expected):
    import asyncio
    assert asyncio.run(nb.resolve_recipients(_Pool(camp, adv), campaign_id="c1")) == expected


def test_resolve_recipients_advertiser_only_and_inactive_owner():
    import asyncio
    assert asyncio.run(nb.resolve_recipients(_Pool(None, _adv(["a@x.io"])), advertiser_id="adv1")) == (["a@x.io"], "advertiser")
    assert asyncio.run(nb.resolve_recipients(_Pool(None, _adv(), user_status="blocked"), advertiser_id="adv1")) == ([], "none")


def test_emit_gates_in_app_and_email_together():
    import asyncio
    pool = _Pool(_camp(["c@x.io"]), _adv())

    async def go():
        first = await nb.emit(pool, "budget_exhausted", dedup_key="k1", entity_type="campaign", entity_id="c1",
                              owner_user_id="u1", campaign_id="c1", title="T", lines=["L1", "L2"], link="/campaigns/c1")
        second = await nb.emit(pool, "budget_exhausted", dedup_key="k1", entity_type="campaign", entity_id="c1",
                               owner_user_id="u1", campaign_id="c1", title="T", lines=["L1"], link="/campaigns/c1")
        return first, second

    assert asyncio.run(go()) == (True, False)
    assert len(pool.notifications) == 1 and pool.notifications[0][1] == "budget_exhausted"
    assert pool.notifications[0][2].startswith("Бюджет исчерпан: T") and pool.notifications[0][4] == "/campaigns/c1"
    assert len(pool.outbox) == 1 and pool.outbox[0][0] == "c@x.io" and pool.outbox[0][5] == "k1"
    snap = obs_metrics.snapshot()["counters"]
    assert snap["notify_business_events_total"] == 1 and snap["notify_business_deduplicated_total"] == 1


def test_emit_without_recipients_still_creates_in_app():
    import asyncio
    pool = _Pool(_camp(inherit=False), _adv(["a@x.io"]))
    assert asyncio.run(nb.emit(pool, "campaign_ended", dedup_key="k2", entity_type="campaign", entity_id="c1",
                               owner_user_id="u1", campaign_id="c1", title="T", lines=[])) is True
    assert len(pool.notifications) == 1 and pool.outbox == []


def test_autorule_fired_dedups_per_hour():
    import asyncio
    pool = _Pool(_camp(), _adv())
    rule = {"id": "r1", "name": "CTR low", "metric": "ctr", "operator": "lt", "threshold": 0.5, "window_hours": 24, "action": "pause_campaign"}

    async def go():
        a = await nb.autorule_fired(pool, rule=rule, campaign_id="c1", value=0.12, message="Кампания c1 приостановлена")
        b = await nb.autorule_fired(pool, rule=rule, campaign_id="c1", value=0.11, message="...")
        return a, b

    assert asyncio.run(go()) == (True, False)
    assert pool.outbox[0][0] == "owner@x.io" and pool.outbox[0][5].startswith("autorule_fired:r1:c1:")
    assert "ctr = 0.12" in pool.outbox[0][2] or "ctr = 0.12" in pool.notifications[0][3]


def test_run_checks_isolates_failures(monkeypatch):
    import asyncio

    async def boom(*a, **k):
        raise RuntimeError("db down")

    async def ok(*a, **k):
        return 2

    monkeypatch.setattr(nb, "_check_budget_exhausted", boom)
    monkeypatch.setattr(nb, "_check_stopped_by_system", ok)
    monkeypatch.setattr(nb, "_check_dates", ok)
    monkeypatch.setattr(nb, "_check_no_impressions", ok)
    monkeypatch.setattr(nb, "_check_low_balance", ok)
    r = asyncio.run(nb.run_checks(object()))
    assert r == {"budget_exhausted": -1, "campaign_stopped_by_system": 2, "dates": 2, "campaign_no_impressions": 2, "low_balance": 2}
    assert obs_metrics.snapshot()["counters"]["notify_business_check_errors_total"] == 1


def test_cron_scheduler_metrics_contract():
    import scheduler
    from routers import cron

    job = next(j for j in scheduler.DEFAULT_JOBS if j["name"] == "notify_business_checks")
    assert job["path"] == "/cron/notify-business-checks" and job["schedule"] == "*/15 * * * *"
    assert "notify_business_checks" in obs_metrics.CRON_JOBS
    assert any(r.path == "/cron/notify-business-checks" for r in cron.router.routes)


def test_system_pause_sites_set_stop_reason():
    root = Path(__file__).resolve().parents[1]
    billing = (root / "billing_core.py").read_text(encoding="utf-8")
    rules = (root / "rules_engine.py").read_text(encoding="utf-8")
    assert "stop_reason = 'balance'" in billing and "stop_reason = 'total_budget'" in billing
    assert "stop_reason = 'autorule'" in rules and "notify_business.autorule_fired" in rules
    camps = (root / "routers" / "campaigns.py").read_text(encoding="utf-8")
    assert 'updates["stop_reason"] = None' in camps
