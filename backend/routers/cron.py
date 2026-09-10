"""Cron endpoints, hit by Vercel Cron in production.

Protected by CRON_SECRET when set (Vercel sends it as a Bearer token).
"""

import functools
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Request

import conversion_attribution as attribution
import obs_metrics
from business_day import business_date
from cache import avail_adv_key, avail_camp_key, clear_stale_daily_spend, get_redis, res_adv_key, res_camp_key
from db import get_pool
from optimizer import run_optimizer
from rules_engine import evaluate_rules_for_user

logger = logging.getLogger(__name__)

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
    """Housekeeping for daily spend counters — idempotent, safe at any hour.

    Бизнес-день = календарный день Europe/Moscow (business_day.py). Граница
    задаётся датой в ключе Redis `spend:*:{YYYY-MM-DD}` и колонкой
    `spent_today_date`, а не моментом запуска этого cron: здесь обнуляются
    только счётчики ПРОШЛЫХ дней. Повторный или опоздавший запуск не трогает
    расход текущего дня (audit M-04).
    """
    _check_secret(request)
    pool = await get_pool()
    today = business_date()
    campaigns = await pool.execute(
        "UPDATE campaigns SET spent_today = 0, spent_today_date = NULL "
        "WHERE spent_today > 0 AND (spent_today_date IS NULL OR spent_today_date < $1)",
        today,
    )
    line_items = await pool.execute(
        "UPDATE line_items SET spent_today = 0, spent_today_date = NULL "
        "WHERE spent_today > 0 AND (spent_today_date IS NULL OR spent_today_date < $1)",
        today,
    )
    redis_keys = await clear_stale_daily_spend(today.isoformat())
    return {
        "business_date": today.isoformat(),
        "campaigns": campaigns,
        "line_items": line_items,
        "redis_keys_cleared": redis_keys,
    }


@router.get("/reconcile-reserves")
@_cron_job("reconcile_reserves")
async def reconcile_reserves(request: Request):
    """Сверка Redis-остатков с БД (audit M-05/M-06). Раз в несколько минут.

    Инвариант: avail:adv:{id} == advertisers.balance - res:adv:{id};
    avail:camp:{id} == total_budget - spent_total - res:camp:{id}.
    Дрейф возможен после потери событий (orphan pending без bidmeta, сбой
    Redis между bill_win и settle_win). Ключи, которых нет, не создаём —
    их посеет следующий бид из снимка фида. Разница считается дрейфом,
    если превышает копейку.
    """
    _check_secret(request)
    pool = await get_pool()
    r = get_redis()
    fixed_adv = fixed_camp = 0
    advs = await pool.fetch(
        "SELECT DISTINCT a.id, a.balance FROM advertisers a JOIN campaigns c ON c.advertiser_id = a.id "
        "WHERE c.status = 'active'"
    )
    for a in advs:
        key = avail_adv_key(str(a["id"]))
        cur = await r.get(key)
        if cur is None:
            continue
        outstanding = float(await r.get(res_adv_key(str(a["id"]))) or 0)
        expected = float(a["balance"]) - max(outstanding, 0.0)
        if abs(float(cur) - expected) > 0.01:
            await r.set(key, repr(expected))
            fixed_adv += 1
    camps = await pool.fetch(
        "SELECT id, total_budget, spent_total FROM campaigns WHERE status = 'active' AND total_budget > 0"
    )
    for c in camps:
        key = avail_camp_key(str(c["id"]))
        cur = await r.get(key)
        if cur is None:
            continue
        outstanding = float(await r.get(res_camp_key(str(c["id"]))) or 0)
        expected = float(c["total_budget"]) - float(c["spent_total"]) - max(outstanding, 0.0)
        if abs(float(cur) - expected) > 0.01:
            await r.set(key, repr(expected))
            fixed_camp += 1
    if fixed_adv or fixed_camp:
        obs_metrics.inc("reserve_reconcile_drift_total", fixed_adv + fixed_camp)
    return {"advertisers_checked": len(advs), "advertisers_fixed": fixed_adv,
            "campaigns_checked": len(camps), "campaigns_fixed": fixed_camp}


@router.get("/sweep-burl")
@_cron_job("sweep_burl")
async def sweep_burl(request: Request):
    """Биллинг по burl (audit R-07): выигрыши SSP с billing_event=burl, чей burl
    не пришёл за BURL_TTL, -> win_expired: резерв освобождается, денег нет,
    событие остаётся для сверки с SSP. Каждую минуту — раньше, чем sweeper
    Go добирается до pending:bids (дедлайн там сдвинут на BURL_SWEEP_GRACE)."""
    _check_secret(request)
    from routers.rtb import sweep_pending_burl

    return await sweep_pending_burl()


@router.get("/audience-sync")
@_cron_job("audience_sync")
async def audience_sync(request: Request):
    """Ретаргетинг-аудитории (audit R-06): удалить просроченных участников в
    Postgres, вычистить просроченные score в audz:*, досеять пустые ключи из
    Postgres (самовосстановление после потери Redis). Раз в час."""
    _check_secret(request)
    from audiences import sync_audiences

    pool = await get_pool()
    result = await sync_audiences(pool)
    if result.get("reseeded_segments"):
        obs_metrics.inc("audience_reseeded_segments_total", result["reseeded_segments"])
    return result


@router.get("/optimize")
@_cron_job("optimize")
async def run_buy_optimizer(request: Request):
    """Analyze traffic quality and block weak sources for auto_optimize campaigns."""
    _check_secret(request)
    # F-02/B14c: gauge conversion_attribution_coverage{dimension} обновляется здесь же —
    # optimizer и алерт смотрят на одну и ту же величину.
    coverage = await attribution.refresh_coverage_gauges(await get_pool())
    result = await run_optimizer()
    result["attribution_coverage"] = coverage
    return result


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
    # Показы были, а зарегистрированного erid нет — в ОРД такое отправить нельзя,
    # но и молча пропускать нельзя: это реклама без маркировки (ФЗ-347).
    skipped_rows = await pool.fetch(
        """SELECT s.creative_id, s.campaign_id,
                  sum(s.impressions) AS impressions,
                  max(oc.status)     AS ord_status
           FROM stats_hourly s
           LEFT JOIN ord_creatives oc ON oc.creative_id = s.creative_id
                AND oc.status IN ('registered', 'superseded')
                AND oc.erid IS NOT NULL AND oc.erid <> ''
           WHERE to_char(s.ts_hour, 'YYYY-MM') = $1
           GROUP BY s.creative_id, s.campaign_id
           HAVING sum(s.impressions) > 0 AND count(oc.id) = 0
           ORDER BY sum(s.impressions) DESC""",
        period,
    )
    skipped_without_erid = [
        {
            "creative_id": str(r["creative_id"]),
            "campaign_id": str(r["campaign_id"]) if r["campaign_id"] else None,
            "impressions": int(r["impressions"]),
        }
        for r in skipped_rows
    ]
    obs_metrics.set_gauge("ord_export_skipped_without_erid", len(skipped_without_erid))
    if skipped_without_erid:
        logger.warning(
            "ord-export %s: %d creatives with impressions but no erid: %s",
            period, len(skipped_without_erid),
            ", ".join(x["creative_id"] for x in skipped_without_erid[:20]),
        )

    log_id = await pool.fetchval(
        """INSERT INTO ord_export_log (period, kind, rows_sent, request)
           VALUES ($1, 'stats', $2, $3) RETURNING id""",
        period, len(stats),
        _json.dumps({"stats": stats, "skipped_without_erid": skipped_without_erid}, ensure_ascii=False),
    )
    if not stats:
        await pool.execute(
            "UPDATE ord_export_log SET status='ok', finished_at=now() WHERE id=$1", log_id
        )
        obs_metrics.set_gauge("ord_export_last_success_timestamp_seconds", time.time())
        return {"period": period, "status": "ok", "rows": 0, "skipped_without_erid": skipped_without_erid}

    try:
        # OrdNotConfigured (production без ключа) — подкласс OrdError: лог failed + 502.
        adapter = await get_ord_adapter()
        out = await adapter.submit_stats(period, stats)
        await pool.execute(
            "UPDATE ord_export_log SET status='ok', response=$2, finished_at=now() WHERE id=$1",
            log_id, _json.dumps(out, ensure_ascii=False),
        )
        obs_metrics.set_gauge("ord_export_last_success_timestamp_seconds", time.time())
        return {
            "period": period,
            "status": "ok",
            "rows": len(stats),
            "provider": adapter.provider,
            "skipped_without_erid": skipped_without_erid,
        }
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
    # Дедуп-заявки трекинг-событий (E-02): event-токен живёт 6 часов, 3 суток —
    # с запасом на ретраи SSP. Не зависит от партиционирования bid_events.
    once_status = await pool.execute(
        "DELETE FROM bid_event_once WHERE created_at < now() - interval '3 days'"
    )
    once_deleted = int(once_status.rsplit(" ", 1)[-1] or 0)
    # Inbox consumer-а Redis Stream (E-03): стрим на стороне Go живёт минуты,
    # 3 суток inbox — с запасом на любой replay/XAUTOCLAIM.
    inbox_status = await pool.execute(
        "DELETE FROM rtb_events_inbox WHERE created_at < now() - interval '3 days'"
    )
    inbox_deleted = int(inbox_status.rsplit(" ", 1)[-1] or 0)

    partitioned = await pool.fetchval(
        """SELECT EXISTS (
               SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relname = 'bid_events' AND c.relkind = 'p')"""
    )
    if not partitioned:
        return {
            "skipped": "bid_events is not partitioned (apply migration 033)",
            "once_deleted": once_deleted,
            "inbox_deleted": inbox_deleted,
        }

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
        "once_deleted": once_deleted,
        "inbox_deleted": inbox_deleted,
    }
