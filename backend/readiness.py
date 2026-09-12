"""Readiness backend (audit O-05): отдельно от liveness.

/health — liveness: процесс и HTTP-сервер отвечают; ни БД, ни Redis, ни consumer
не проверяются. Его и только его опрашивает Docker HEALTHCHECK, чтобы отказ
зависимости не превращал живой процесс в unhealthy.

/ready — readiness: PostgreSQL ping, Redis ping, живость и lag consumer'а
rtb:events. 503 с безопасной разбивкой причин — для deploy gate
(preprod_check), readiness-мониторинга и будущего балансировщика. Наружу уходят
только короткие коды состояний; текст исключений, адреса и DSN — в логах.

Тот же расчёт раз в REFRESH_EVERY_S пишет gauges backend_ready /
backend_postgres_up / backend_redis_up, чтобы Prometheus видел неготовность
без внешнего пробера.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import events_consumer
import startup_checks
import obs_metrics

logger = logging.getLogger("readiness")

PG_TIMEOUT_S = float(os.getenv("READY_PG_TIMEOUT_S", "2"))
REDIS_TIMEOUT_S = float(os.getenv("READY_REDIS_TIMEOUT_S", "1"))
# Порог lag = critical в ops/prometheus/alerts.yml (RtbEventsLagCritical).
CONSUMER_MAX_LAG = int(os.getenv("READY_MAX_EVENTS_LAG", "150000"))
# Consumer делает успешную итерацию не реже BLOCK_MS (1 с) даже на пустом
# стриме; минута без успеха — завис или крутится в backoff.
CONSUMER_STALE_S = float(os.getenv("READY_CONSUMER_STALE_S", "60"))
REFRESH_EVERY_S = float(os.getenv("READY_REFRESH_EVERY_S", "15"))

# Коды состояний — единственное, что уходит в тело /ready.
OK = "ok"
ERROR = "error"
TIMEOUT = "timeout"
NOT_CONFIGURED = "not_configured"
DISABLED = "disabled"
STARTING = "starting"
NOT_RUNNING = "not_running"
STALE = "stale"
LAGGING = "lagging"


@dataclass
class Report:
    ready: bool
    checks: dict[str, str] = field(default_factory=dict)

    def body(self) -> dict:
        return {"status": OK if self.ready else "not_ready", "checks": dict(self.checks)}


async def check_postgres() -> str:
    from db import get_pool

    try:
        pool = await asyncio.wait_for(get_pool(), timeout=PG_TIMEOUT_S)
        await asyncio.wait_for(pool.fetchval("SELECT 1"), timeout=PG_TIMEOUT_S)
        return OK
    except asyncio.TimeoutError:
        logger.warning("readiness: postgres ping timed out after %.1fs", PG_TIMEOUT_S)
        return TIMEOUT
    except Exception as e:  # noqa: BLE001 — любой отказ БД = не готов; детали только в лог
        logger.warning("readiness: postgres ping failed: %s", e)
        return ERROR


async def check_redis() -> str:
    if not (os.environ.get("REDIS_URL") or os.environ.get("KV_URL")):
        return NOT_CONFIGURED
    from cache import get_redis

    try:
        await asyncio.wait_for(get_redis().ping(), timeout=REDIS_TIMEOUT_S)
        return OK
    except asyncio.TimeoutError:
        logger.warning("readiness: redis ping timed out after %.1fs", REDIS_TIMEOUT_S)
        return TIMEOUT
    except Exception as e:  # noqa: BLE001
        logger.warning("readiness: redis ping failed: %s", e)
        return ERROR


def check_events_consumer() -> str:
    st = events_consumer.status()
    if not st["enabled"]:
        return DISABLED
    if not st["running"]:
        return NOT_RUNNING
    if st["last_ok_age_s"] is None:
        return STARTING
    if st["last_ok_age_s"] > CONSUMER_STALE_S:
        return STALE
    if st["lag"] is not None and st["lag"] > CONSUMER_MAX_LAG:
        return LAGGING
    return OK


def _redis_acceptable(state: str) -> bool:
    # Без REDIS_URL backend работает только в dev; в production конфиг обязателен.
    return state == OK or (state == NOT_CONFIGURED and not startup_checks.is_production())


async def evaluate() -> Report:
    pg, rd = await asyncio.gather(check_postgres(), check_redis())
    consumer = check_events_consumer()
    ready = pg == OK and _redis_acceptable(rd) and consumer in (OK, DISABLED)
    report = Report(ready=ready, checks={"postgres": pg, "redis": rd, "events_consumer": consumer})
    obs_metrics.set_gauge("backend_postgres_up", 1.0 if pg == OK else 0.0)
    obs_metrics.set_gauge("backend_redis_up", 1.0 if rd == OK else 0.0)
    # disabled = consumer сознательно не в этом процессе (RTB_EVENTS_CONSUMER=0) — не сбой.
    obs_metrics.set_gauge("rtb_events_consumer_up", 1.0 if consumer in (OK, DISABLED) else 0.0)
    obs_metrics.set_gauge("backend_ready", 1.0 if ready else 0.0)
    return report


async def run_forever(stop: asyncio.Event) -> None:
    """Фоновый пересчёт gauges. Переходы ready<->not_ready — в лог с разбивкой."""
    was_ready: bool | None = None
    while not stop.is_set():
        try:
            report = await evaluate()
            if report.ready != was_ready:
                logger.log(
                    logging.INFO if report.ready else logging.WARNING,
                    "readiness: %s %s", "ready" if report.ready else "NOT ready", report.checks,
                )
                was_ready = report.ready
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — цикл не должен умирать из-за одной итерации
            logger.exception("readiness refresh failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=REFRESH_EVERY_S)
        except asyncio.TimeoutError:
            pass


_task: asyncio.Task | None = None
_stop = asyncio.Event()


def start_background() -> None:
    global _task
    _stop.clear()
    _task = asyncio.get_event_loop().create_task(run_forever(_stop), name="readiness-refresh")


async def stop_background() -> None:
    global _task
    _stop.set()
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None
