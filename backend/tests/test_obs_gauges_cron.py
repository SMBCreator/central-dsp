"""D8: метрики для новых алертов — gauges, cron-задачи, классы HTTP-ответов."""

import pytest

import obs_metrics


@pytest.fixture(autouse=True)
def _reset():
    obs_metrics.reset_for_tests()
    yield
    obs_metrics.reset_for_tests()


def test_gauge_absent_until_set_then_rendered():
    out = obs_metrics.render_prometheus()
    assert "bid_events_default_partition_rows" not in out  # absent() в PromQL
    obs_metrics.set_gauge("bid_events_default_partition_rows", 0)
    out = obs_metrics.render_prometheus()
    assert "# TYPE bid_events_default_partition_rows gauge" in out
    assert "\nbid_events_default_partition_rows 0\n" in out
    obs_metrics.set_gauge("bid_events_default_partition_rows", 42)
    assert "\nbid_events_default_partition_rows 42\n" in obs_metrics.render_prometheus()


def test_unknown_gauge_and_job_fail_fast():
    with pytest.raises(KeyError):
        obs_metrics.set_gauge("typo_gauge", 1)
    with pytest.raises(KeyError):
        obs_metrics.record_cron_run("typo_job", ok=True)


def test_cron_runs_and_last_success_bounded_labels():
    obs_metrics.record_cron_run("retention", ok=True, now=1_700_000_000.0)
    obs_metrics.record_cron_run("retention", ok=False)
    obs_metrics.record_cron_run("ord_export", ok=False)
    out = obs_metrics.render_prometheus()
    assert 'cron_job_runs_total{cron_job="retention",result="ok"} 1' in out
    assert 'cron_job_runs_total{cron_job="retention",result="error"} 1' in out
    assert 'cron_job_runs_total{cron_job="ord_export",result="error"} 1' in out
    assert 'cron_job_last_success_timestamp_seconds{cron_job="retention"} 1700000000.000' in out
    # Ошибка не двигает last_success; задача без успешных запусков — absent.
    assert 'cron_job_last_success_timestamp_seconds{cron_job="ord_export"}' not in out
    # Все job/result присутствуют как нули — фиксированная кардинальность.
    for job in obs_metrics.CRON_JOBS:
        for res in ("ok", "error"):
            assert f'cron_job_runs_total{{cron_job="{job}",result="{res}"}}' in out


def test_http_status_classes():
    for code in (200, 201, 301, 404, 429, 500, 503, 599, 999):
        obs_metrics.inc_http_status(code)
    snap = obs_metrics.snapshot()["http"]
    assert snap == {"1xx": 0, "2xx": 2, "3xx": 1, "4xx": 2, "5xx": 4}
    out = obs_metrics.render_prometheus()
    assert 'http_responses_total{class="5xx"} 4' in out
    assert 'http_responses_total{class="1xx"} 0' in out


def test_ord_export_failures_counter_exists():
    obs_metrics.inc("ord_export_failures_total")
    assert "ord_export_failures_total 1" in obs_metrics.render_prometheus()


@pytest.mark.asyncio
async def test_cron_job_decorator_records_outcomes():
    from fastapi import HTTPException

    from routers.cron import _cron_job

    @_cron_job("shading")
    async def ok_job():
        return {"ok": True}

    @_cron_job("shading")
    async def boom():
        raise RuntimeError("db down")

    @_cron_job("shading")
    async def unauthorized():
        raise HTTPException(status_code=401, detail="Invalid cron secret")

    @_cron_job("shading")
    async def upstream_failed():
        raise HTTPException(status_code=502, detail="ORD failed")

    assert await ok_job() == {"ok": True}
    with pytest.raises(RuntimeError):
        await boom()
    with pytest.raises(HTTPException):
        await unauthorized()
    with pytest.raises(HTTPException):
        await upstream_failed()
    runs = obs_metrics.snapshot()["cron_runs"]
    assert runs[("shading", "ok")] == 1
    # RuntimeError + 502 — ошибки задачи; 401 (секрет) — нет.
    assert runs[("shading", "error")] == 2
    assert obs_metrics.snapshot()["cron_last_ok"]["shading"] is not None


def test_metrics_endpoint_counts_http_classes_but_not_itself():
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        obs_metrics.reset_for_tests()
        assert client.get("/health").status_code == 200
        assert client.get("/definitely-missing-route").status_code == 404
        body = client.get("/metrics").text
    assert 'http_responses_total{class="2xx"} 1' in body
    assert 'http_responses_total{class="4xx"} 1' in body
    # Сам скрейп /metrics не учитывается — иначе каждая проверка двигает знаменатель.
    body2_snapshot = obs_metrics.snapshot()["http"]
    assert body2_snapshot["2xx"] == 1


@pytest.mark.asyncio
async def test_retention_publishes_default_gauge_before_ensure_partitions(monkeypatch):
    """O-06: gauge DEFAULT-партиции публикуется ДО создания партиций — если
    ensure_partitions упадёт, диагностика уже снята (раньше было наоборот)."""
    import notify_outbox
    import routers.cron as cron

    calls: list[str] = []

    class _Pool:
        async def execute(self, sql, *args):
            return "DELETE 0"

        async def fetchval(self, sql, *args):
            calls.append(sql)
            if "pg_class" in sql:
                return True
            if "count(*) FROM bid_events_default" in sql:
                return 7
            if "ensure_partitions" in sql:
                raise RuntimeError("updated partition constraint for default partition would be violated")
            raise AssertionError(sql)

    async def _pool():
        return _Pool()

    async def _purge(pool, **kw):
        return 0

    monkeypatch.setattr(cron, "get_pool", _pool)
    monkeypatch.setattr(notify_outbox, "purge_old", _purge)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.setenv("APP_ENV", "test")

    class _Req:
        headers: dict = {}
        query_params: dict = {}

    with pytest.raises(RuntimeError):
        await cron.bid_events_retention(_Req())  # type: ignore[arg-type]
    assert "\nbid_events_default_partition_rows 7\n" in obs_metrics.render_prometheus()
    ensure_idx = next(i for i, s in enumerate(calls) if "ensure_partitions" in s)
    count_idx = next(i for i, s in enumerate(calls) if "count(*) FROM bid_events_default" in s)
    assert count_idx < ensure_idx
