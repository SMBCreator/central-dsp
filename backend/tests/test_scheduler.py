"""Планировщик (audit O-01, C1b) — инварианты без БД.

* DEFAULT_JOBS покрывает все задачи из obs_metrics.CRON_JOBS, каждая ведёт на
  существующий маршрут routers/cron.py, расписания валидны;
* run_job классифицирует исходы HTTP: ok / error / timeout / unreachable,
  timeout_seconds — общий бюджет запроса, а не пауза между байтами;
* захват ограничен свободными слотами (lease не протухает в очереди);
* heartbeat продлевает lease и фиксирует его потерю;
* config_problems — fail-fast на пустых CRON_SECRET/DATABASE_URL/BACKEND_INTERNAL_URL.

vercel.json намеренно НЕ сверяется с DEFAULT_JOBS: Vercel — preview/frontend,
не источник production-расписания (решение C1b).
"""
import asyncio

import httpx

import obs_metrics
import scheduler
from cronexpr import CronExpr
from routers.cron import router as cron_router


def test_default_jobs_match_metrics_and_routes():
    names = [j["name"] for j in scheduler.DEFAULT_JOBS]
    assert len(names) == len(set(names))
    assert set(names) == set(obs_metrics.CRON_JOBS)
    routes = {r.path for r in cron_router.routes}
    for j in scheduler.DEFAULT_JOBS:
        assert j["path"] in routes, j
        CronExpr.parse(j["schedule"])
        assert 5 <= j["timeout_seconds"] <= 21600
        assert j["description"]


def test_lease_outlives_several_heartbeats():
    # один пропущенный heartbeat (сбой БД) не должен отдавать задачу другому воркеру
    assert scheduler.LEASE_SECONDS >= 3 * scheduler.HEARTBEAT_SECONDS
    assert scheduler.HEARTBEAT_SECONDS < min(j["timeout_seconds"] for j in scheduler.DEFAULT_JOBS)


def test_every_minute_job_has_timeout_below_a_minute():
    for j in scheduler.DEFAULT_JOBS:
        if j["schedule"] == "* * * * *":
            assert j["timeout_seconds"] < 60, j["name"]


def _job(timeout=5):
    return scheduler.Claimed("rules", "/cron/rules", "0 6 * * *", timeout)


def _run(handler, job=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await scheduler.run_job(client, job or _job(), "http://backend:8000/", "s3cret")

    return asyncio.run(go())


def test_run_job_ok_sends_bearer():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"ok": True})

    status, code, ms, err = _run(handler)
    assert (status, code, err) == ("ok", 200, None)
    assert ms >= 0
    assert seen == {"auth": "Bearer s3cret", "url": "http://backend:8000/cron/rules"}


def test_run_job_http_error_is_error_with_body():
    status, code, _, err = _run(lambda r: httpx.Response(401, json={"detail": "Invalid cron secret"}))
    assert (status, code) == ("error", 401)
    assert "HTTP 401" in err and "Invalid cron secret" in err


def test_run_job_timeout_and_unreachable():
    def timeout(_):
        raise httpx.ReadTimeout("slow")

    def down(_):
        raise httpx.ConnectError("refused")

    assert (_run(timeout))[0] == "timeout"
    st, code, _, err = _run(down)
    assert (st, code) == ("unreachable", None) and "ConnectError" in err


def test_run_job_timeout_is_total_budget_not_idle_gap():
    # backend отвечает медленно, но без пауз между байтами дольше read-таймаута:
    # раньше такой запрос жил бесконечно, теперь режется общим бюджетом.
    async def slow(_):
        await asyncio.sleep(1.0)
        return httpx.Response(200, json={"ok": True})

    st, code, ms, err = _run(slow, job=scheduler.Claimed("rules", "/cron/rules", "0 6 * * *", 0.2))
    assert (st, code) == ("timeout", None) and "0.2s" in err
    assert ms < 900


class _FakePool:
    """pool.acquire() -> conn с execute(), возвращающим заданный tag."""

    def __init__(self, tag="UPDATE 1"):
        self.tag = tag
        self.calls: list[tuple] = []
        pool = self

        class _Conn:
            async def execute(self, sql, *args):
                pool.calls.append((sql, args))
                return pool.tag

        class _Ctx:
            async def __aenter__(self):
                return _Conn()

            async def __aexit__(self, *exc):
                return False

        self._ctx = _Ctx

    def acquire(self):
        return self._ctx()


def test_claim_limit_is_free_slots_and_zero_skips_query():
    async def go():
        pool = _FakePool()
        sched = scheduler.Scheduler(pool, base_url="http://b", secret="s", worker="W", concurrency=2)
        assert sched.free_slots == 2
        never_done = asyncio.get_running_loop().create_future()
        sched._inflight.add(never_done)  # type: ignore[arg-type]
        sched._inflight.add(asyncio.get_running_loop().create_future())  # type: ignore[arg-type]
        assert sched.free_slots == 0
        assert await scheduler.claim_due(None, "W", limit=sched.free_slots) == []  # conn не трогается
        assert await scheduler.claim_due(None, "W", limit=-1) == []

    asyncio.run(go())


def test_heartbeat_extends_lease_and_detects_takeover(monkeypatch):
    async def go():
        obs_metrics.reset_for_tests()
        pool = _FakePool("UPDATE 1")
        sched = scheduler.Scheduler(pool, base_url="http://b", secret="s", worker="W", concurrency=1)
        sched.heartbeat_seconds = 0.02

        async def slow_job(client, job, base_url, secret):
            await asyncio.sleep(0.1)
            return "ok", 200, 100, None

        monkeypatch.setattr(scheduler, "run_job", slow_job)
        job = _job()
        await sched._execute(job)
        hb = [c for c in pool.calls if "SET locked_at = now()" in c[0]]
        assert len(hb) >= 2 and all(c[1] == ("rules", "W") for c in hb)
        done = [c for c in pool.calls if "runs_total = runs_total + 1" in c[0]]
        assert len(done) == 1 and done[0][1][-1] == "W"  # complete() защищён locked_by = worker
        assert obs_metrics.snapshot()["counters"]["scheduler_lease_lost_total"] == 0

        # lease перехвачен: heartbeat видит UPDATE 0 -> метрика, цикл останавливается
        pool.tag = "UPDATE 0"
        pool.calls.clear()
        await sched._execute(job)
        assert obs_metrics.snapshot()["counters"]["scheduler_lease_lost_total"] == 1
        assert sum("SET locked_at = now()" in c[0] for c in pool.calls) == 1  # после потери не долбим БД
        obs_metrics.reset_for_tests()

    asyncio.run(go())


def test_config_problems():
    assert scheduler.config_problems({}) and len(scheduler.config_problems({})) == 3
    ok = {"DATABASE_URL": "postgresql://x", "CRON_SECRET": "s", "BACKEND_INTERNAL_URL": "http://backend:8000"}
    assert scheduler.config_problems(ok) == []
    assert scheduler.config_problems({**ok, "CRON_SECRET": "  "})


def test_scheduler_metrics_registered():
    obs_metrics.reset_for_tests()
    obs_metrics.set_gauge("scheduler_last_tick_timestamp_seconds", 1.0)
    obs_metrics.set_gauge("scheduler_jobs_overdue", 0)
    obs_metrics.set_gauge("scheduler_jobs_inflight", 2)
    obs_metrics.inc("scheduler_job_failures_total")
    obs_metrics.inc("scheduler_lease_lost_total")
    out = obs_metrics.render_prometheus()
    assert "scheduler_last_tick_timestamp_seconds 1" in out
    assert "scheduler_job_failures_total 1" in out
    assert "scheduler_lease_lost_total 1" in out and "scheduler_jobs_inflight 2" in out
    obs_metrics.reset_for_tests()
