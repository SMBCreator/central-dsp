"""Cron endpoints, hit by Vercel Cron in production.

Protected by CRON_SECRET when set (Vercel sends it as a Bearer token).
"""

import functools
import os
import time

from fastapi import APIRouter, HTTPException, Request

import obs_metrics
from cache import clear_daily_spend
from db import get_pool
from optimizer import run_optimizer
from rules_engine import evaluate_rules_for_user

router = APIRouter(prefix="/cron", tags=["cron"])


def _check_secret(request: Request) -> None:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        # Без секрета ручки открыты всем — допустимо только в dev. В production
        # startup_checks не даст процессу подняться; здесь второй слой на случай
        # запуска мимо lifespan (например, uvicorn --factory / тесты).
        if os.environ.get("APP_ENV", "").strip().lower() in ("production", "prod"):
            raise HTTPException(status_code=503, detail="CRON_SECRET is not configured")
        return
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="Invalid cron secret")


def _cron_job(name: str):
    """Учёт исхода запуска в obs_metrics (D8: алерты CronJobFailing /
    CronRetentionStale). Отказ по секрету (401/503) — не «падение задачи»,
    поэтому HTTPException с этими кодами не считается ошибкой."""

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                result = await fn(*args, **kwargs)
            except HTTPException as e:
                if e.status_code not in (401, 503):
                    obs_metrics.record_cron_run(name, ok=False)
                raise
            except Exception:
                obs_metrics.record_cron_run(name, ok=False)
                raise
            obs_metrics.record_cron_run(name, ok=True)
            return result

        return wrapper

    return deco


async def seed_gauges_from_db() -> None:
    """Однократно при старте: gauge последнего успешного ОРД-экспорта из
    ord_export_log, чтобы после рестарта процесса OrdExportLag не молчал
    (absent) до ближайшего запуска cron. Любая ошибка БД — пропуск."""
    pool = await get_pool()
    ts = await pool.fetchval(
        "SELECT extract(epoch FROM max(finished_at)) FROM ord_export_log WHERE status = 'ok'"
    )
    if ts is not None:
        obs_metrics.set_gauge("ord_export_last_success_timestamp_seconds", float(ts))


@router.get("/rules")
@_cron_job("rules")
async def run_all_rules(request: Request):
    """Evaluate auto-rules for every user with active rules."""
    _check_secret(request)
    pool = await get_pool()
    user_ids = await pool.fetch("SELECT DISTINCT user_id FROM rules WHERE status = 'active'")
    total_triggered = 0
    for row in user_ids:
        triggered = await evaluate_rules_for_user(str(row["user_id"]))
        total_triggered += len(triggered)
    return {"users_checked": len(user_ids), "rules_triggered": total_triggered}


@router.get("/reset-daily")
@_cron_job("reset_daily")
async def reset_daily_budgets(request: Request):
    """Reset daily spend counters in Postgres AND Redis.

    Расписание в vercel.json — 21:00 UTC, что соответствует 00:00 МСК (UTC+3):
    дневные бюджеты обнуляются по московской полуночи, как в кабинете.
    """
    _check_secret(request)
    pool = await get_pool()
    campaigns = await pool.execute("UPDATE campaigns SET spent_today = 0 WHERE spent_today > 0")
    line_items = await pool.execute("UPDATE line_items SET spent_today = 0 WHERE spent_today > 0")
    redis_keys = await clear_daily_spend()
    return {"campaigns": campaigns, "line_items": line_items, "redis_keys_cleared": redis_keys}


@router.get("/optimize")
@_cron_job("optimize")
async def run_buy_optimizer(request: Request):
    """Analyze traffic quality and block weak sources for auto_optimize campaigns."""
    _check_secret(request)
    return await run_optimizer()


@router.get("/fx-refresh")
@_cron_job("fx_refresh")
async def fx_refresh(request: Request):
    """Ежедневное обновление курса ЦБ (дергается Vercel Cron)."""
    _check_secret(request)
    import fx
    rates = await fx.refresh_rates()
    return {"ok": True, "rates": rates}


@router.get("/shading")
@_cron_job("shading")
async def shading_recompute(request: Request):
    """Ежечасный пересчёт факторов bid-шейдинга по (кампания, SSP)."""
    _check_secret(request)
    import shading
    result = await shading.recompute_factors()
    return {"ok": True, **result}


@router.get("/ml-train")
@_cron_job("ml_train")
async def ml_train(request: Request):
    """Переобучение pCTR-модели по окну bid_events (Часть B).

    Идемпотентно по смыслу: каждая тренировка создаёт новую версию в
    ml_models, прежняя переводится в retired. При недостатке данных — no-op
    с явной причиной (модель не создаётся, биддер работает по базовой цене).
    """
    _check_secret(request)
    from ml import pctr

    pool = await get_pool()
    result = await pctr.train(pool)
    return {"ok": True, **result}


@router.get("/domain-quality")
@_cron_job("domain_quality")
async def domain_quality_recompute(request: Request):
    """Пересчёт скоринга качества доменов для clean-traffic режима (Часть B)."""
    _check_secret(request)
    from ml import quality

    pool = await get_pool()
    result = await quality.recompute(pool)
    return {"ok": True, **result}


@router.get("/ord-export")
@_cron_job("ord_export")
async def ord_monthly_export(request: Request, period: str | None = None):
    """Месячная выгрузка статистики показов в ОРД (ФЗ-347).

    period 'YYYY-MM'; по умолчанию — предыдущий месяц. Идемпотентно:
    повторный запуск за период, у которого уже есть статус ok, — no-op.
    """
    _check_secret(request)
    import json as _json
    from datetime import datetime, timezone

    from ord import OrdError, get_ord_adapter

    now = datetime.now(timezone.utc)
    if period is None:
        year, month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
        period = f"{year:04d}-{month:02d}"

    pool = await get_pool()
    already = await pool.fetchval(
        "SELECT 1 FROM ord_export_log WHERE period = $1 AND kind = 'stats' AND status = 'ok'",
        period,
    )
    if already:
        obs_metrics.set_gauge("ord_export_last_success_timestamp_seconds", time.time())
        return {"period": period, "status": "ok", "skipped": "already exported"}

    rows = await pool.fetch(
        """SELECT oc.erid, oc.ord_external_id,
                  sum(s.impressions) AS impressions,
                  sum(s.clicks)      AS clicks,
                  sum(s.spend)       AS spend
           FROM stats_hourly s
           JOIN ord_creatives oc ON oc.creative_id = s.creative_id
                AND oc.status IN ('registered', 'superseded')
           WHERE oc.erid IS NOT NULL AND oc.erid <> ''
             AND to_char(s.ts_hour, 'YYYY-MM') = $1
           GROUP BY oc.erid, oc.ord_external_id
           HAVING sum(s.impressions) > 0""",
        period,
    )
    stats = [
        {
            "erid": r["erid"],
            "creative_external_id": r["ord_external_id"],
            "impressions": int(r["impressions"]),
            "clicks": int(r["clicks"]),
            "amount": str(r["spend"]),
        }
        for r in rows
    ]
    log_id = await pool.fetchval(
        """INSERT INTO ord_export_log (period, kind, rows_sent, request)
           VALUES ($1, 'stats', $2, $3) RETURNING id""",
        period, len(stats), _json.dumps(stats, ensure_ascii=False),
    )
    if not stats:
        await pool.execute(
            "UPDATE ord_export_log SET status='ok', finished_at=now() WHERE id=$1", log_id
        )
        obs_metrics.set_gauge("ord_export_last_success_timestamp_seconds", time.time())
        return {"period": period, "status": "ok", "rows": 0}

    adapter = await get_ord_adapter()
    try:
        out = await adapter.submit_stats(period, stats)
        await pool.execute(
            "UPDATE ord_export_log SET status='ok', response=$2, finished_at=now() WHERE id=$1",
            log_id, _json.dumps(out, ensure_ascii=False),
        )
        obs_metrics.set_gauge("ord_export_last_success_timestamp_seconds", time.time())
        return {"period": period, "status": "ok", "rows": len(stats), "provider": adapter.provider}
    except OrdError as e:
        await pool.execute(
            "UPDATE ord_export_log SET status='failed', error=$2, finished_at=now() WHERE id=$1",
            log_id, str(e),
        )
        obs_metrics.inc("ord_export_failures_total")
        raise HTTPException(status_code=502, detail=f"ОРД выгрузка не удалась: {e}")


@router.get("/retention")
@_cron_job("retention")
async def bid_events_retention(request: Request):
    """Ретенция сырого лога bid_events (миграция 033, партиции по дням).

    1. Создаёт дневные партиции на 7 дней вперёд (чтобы события никогда не
       падали в DEFAULT-партицию).
    2. DROP партиций старше BID_EVENTS_RETENTION_DAYS (по умолчанию 90).
       Агрегаты stats_hourly/domain_stats не затрагиваются — отчёты за
       длинные периоды строятся по ним, сырой лог нужен для ML/скоринга/аудита
       в пределах окна.

    Если таблица ещё не партиционирована (миграция 033 не накатана) — no-op
    с пометкой, а не ошибка: cron не должен «краснеть» из-за порядка деплоя.
    """
    _check_secret(request)
    keep_days = int(os.environ.get("BID_EVENTS_RETENTION_DAYS", "90"))
    if keep_days < 7:
        raise HTTPException(status_code=400, detail="BID_EVENTS_RETENTION_DAYS must be >= 7")

    pool = await get_pool()
    partitioned = await pool.fetchval(
        """SELECT EXISTS (
               SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relname = 'bid_events' AND c.relkind = 'p')"""
    )
    if not partitioned:
        return {"skipped": "bid_events is not partitioned (apply migration 033)"}

    created = await pool.fetchval(
        "SELECT bid_events_ensure_partitions(current_date, current_date + 7)"
    )
    dropped = await pool.fetchval("SELECT bid_events_drop_old_partitions($1)", keep_days)
    # Строки в DEFAULT-партиции — признак, что cron создания партиций отставал.
    in_default = await pool.fetchval("SELECT count(*) FROM bid_events_default")
    obs_metrics.set_gauge("bid_events_default_partition_rows", int(in_default or 0))
    return {
        "keep_days": keep_days,
        "partitions_created": created,
        "partitions_dropped": list(dropped or []),
        "rows_in_default_partition": in_default,
    }
