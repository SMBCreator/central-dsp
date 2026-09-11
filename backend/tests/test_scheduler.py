"""Планировщик (audit O-01, C1b) — инварианты без БД.

* DEFAULT_JOBS покрывает все задачи из obs_metrics.CRON_JOBS, каждая ведёт на
  существующий маршрут routers/cron.py, расписания валидны;
* vercel.json (Vercel-preview) не расходится с DEFAULT_JOBS по расписанию;
* run_job классифицирует исходы HTTP: ok / error / timeout / unreachable;
* config_problems — fail-fast на пустых CRON_SECRET/DATABASE_URL/BACKEND_INTERNAL_URL.
"""
import asyncio
import json
from pathlib import Path

import httpx

import obs_metrics
import scheduler
from cronexpr import CronExpr
from routers.cron import router as cron_router

ROOT = Path(__file__).resolve().parents[2]


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


def test_vercel_json_schedules_agree_with_defaults():
    crons = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["crons"]
    by_path = {j["path"]: j for j in scheduler.DEFAULT_JOBS}
    for c in crons:
        path = c["path"].removeprefix("/api")
        assert path in by_path, path
        assert by_path[path]["schedule"] == c["schedule"], path
        assert by_path[path].get("enabled", True), f"{path} enabled on Vercel but disabled by default"


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


def test_config_problems():
    assert scheduler.config_problems({}) and len(scheduler.config_problems({})) == 3
    ok = {"DATABASE_URL": "postgresql://x", "CRON_SECRET": "s", "BACKEND_INTERNAL_URL": "http://backend:8000"}
    assert scheduler.config_problems(ok) == []
    assert scheduler.config_problems({**ok, "CRON_SECRET": "  "})


def test_scheduler_metrics_registered():
    obs_metrics.reset_for_tests()
    obs_metrics.set_gauge("scheduler_last_tick_timestamp_seconds", 1.0)
    obs_metrics.set_gauge("scheduler_jobs_overdue", 0)
    obs_metrics.inc("scheduler_job_failures_total")
    out = obs_metrics.render_prometheus()
    assert "scheduler_last_tick_timestamp_seconds 1" in out
    assert "scheduler_job_failures_total 1" in out
    obs_metrics.reset_for_tests()
