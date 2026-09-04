"""Consumer событий Go-биддера: Redis Stream rtb:events -> bid_events + stats_hourly.

Почему это нужно. Go-биддер не ходит в Postgres (латентность торгов), поэтому
события «bid» он складывает в Redis Stream (bidder-go/internal/redisx.EmitEvent).
Без переноса этих событий в БД у кабинета нет знаменателя для win-rate и eCPM,
а отчёты «по площадкам» пусты. Этот модуль — единственный писатель строк
event_type IN ('bid','loss') для Go-трафика.

Схема надёжности:
  * consumer group «backend» — at-least-once; XACK только после успешного COMMIT;
  * при старте сначала дочитываем pending (id «0») — то, что не подтвердили до
    рестарта, — затем переходим на новые («>»);
  * дубликаты bid_id в bid_events допустимы (таблица — журнал; агрегаты стат
    считаются из stats_hourly, куда мы пишем один раз на батч);
  * стрим ограничен MAXLEN на стороне Go; курсор в rtb_events_cursor — для
    восстановления группы, если Redis потерял её (flush/replace).

Совместимость полей с rtb.py:_log_event: имена колонок те же плюс 034.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger("events_consumer")

STREAM = "rtb:events"          # = redisx.EventStreamKey
GROUP = "backend"
CONSUMER = os.getenv("HOSTNAME", "backend-1")
BATCH = int(os.getenv("RTB_EVENTS_BATCH", "500"))
BLOCK_MS = int(os.getenv("RTB_EVENTS_BLOCK_MS", "1000"))

_UUID_FIELDS = ("campaign_id", "line_item_id", "creative_id", "supply_partner_id")


def _dec(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _ts(v: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(tz=timezone.utc)


def _uuid_or_none(v: Any) -> str | None:
    """Go шлёт строки; пустая строка в UUID-колонку упадёт — нормализуем."""
    return v or None


def normalize(fields: dict[str, Any]) -> dict[str, Any] | None:
    """Поля стрима (строки) -> значения для INSERT. None = событие невалидно."""
    et = fields.get("type")
    if et not in ("bid", "loss"):
        return None
    bid_id = fields.get("bid_id") or ""
    if not bid_id:
        return None
    row: dict[str, Any] = {
        "event_type": et,
        "bid_id": bid_id,
        "imp_id": fields.get("imp_id") or None,
        "price": _dec(fields.get("price")),
        "device_type": fields.get("device_type") or None,
        "geo_country": fields.get("geo_country") or None,
        "geo_region": fields.get("geo_region") or None,
        "geo_city": fields.get("geo_city") or None,
        "ad_type": fields.get("ad_type") or None,
        "domain": (fields.get("domain") or "").strip().lower() or None,
        "bundle": fields.get("bundle") or None,
        "publisher_id": fields.get("publisher_id") or None,
        "tagid": fields.get("tagid") or None,
        "os": fields.get("os") or None,
        "browser": fields.get("browser") or None,
        "bidfloor": _dec(fields.get("bidfloor")),
        "loss_reason": fields.get("loss_reason") or None,
        "min_to_win": _dec(fields.get("min_to_win")),
        "is_test": str(fields.get("is_test", "0")) == "1",
        "created_at": _ts(fields.get("ts")),
    }
    for f in _UUID_FIELDS:
        row[f] = _uuid_or_none(fields.get(f))
    return row


_INSERT_SQL = """
INSERT INTO bid_events (event_type, bid_id, imp_id, campaign_id, line_item_id, creative_id,
                        supply_partner_id, price, device_type, geo_country, geo_region, geo_city,
                        ad_type, domain, bundle, publisher_id, tagid, os, browser, bidfloor,
                        loss_reason, min_to_win, is_test, created_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24)
"""

_INSERT_COLS = (
    "event_type", "bid_id", "imp_id", "campaign_id", "line_item_id", "creative_id",
    "supply_partner_id", "price", "device_type", "geo_country", "geo_region", "geo_city",
    "ad_type", "domain", "bundle", "publisher_id", "tagid", "os", "browser", "bidfloor",
    "loss_reason", "min_to_win", "is_test", "created_at",
)

# stats_hourly.bids — знаменатель win-rate. Пишем агрегатом по батчу (один upsert
# на ключ), а не по строке: при 2-3k QPS это разница между 3k и ~50 запросами/с.
_BUMP_BIDS_SQL = """
INSERT INTO stats_hourly (ts_hour, campaign_id, line_item_id, creative_id, supply_partner_id, bids, spend)
VALUES ($1, $2, $3, $4, $5, $6, 0)
ON CONFLICT (ts_hour, campaign_id, line_item_id, creative_id, supply_partner_id)
DO UPDATE SET bids = stats_hourly.bids + EXCLUDED.bids
"""


def aggregate_bids(rows: list[dict[str, Any]]) -> list[tuple]:
    """(hour, campaign, li, creative, ssp) -> count для event_type=bid и не test."""
    agg: dict[tuple, int] = defaultdict(int)
    for r in rows:
        if r["event_type"] != "bid" or r["is_test"]:
            continue
        hour = r["created_at"].replace(minute=0, second=0, microsecond=0)
        key = (hour, r["campaign_id"], r["line_item_id"], r["creative_id"], r["supply_partner_id"])
        agg[key] += 1
    return [(*k, n) for k, n in agg.items()]


async def persist_batch(pool, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(_INSERT_SQL, [tuple(r[c] for c in _INSERT_COLS) for r in rows])
            bumps = aggregate_bids(rows)
            if bumps:
                await conn.executemany(_BUMP_BIDS_SQL, bumps)


async def _ensure_group(redis, pool) -> None:
    """Создать consumer group. Если группа потеряна (Redis пересоздан), стартуем
    с курсора из БД, иначе с «$» (только новые — стрим ограничен MAXLEN, догонять
    старое смысла нет: bidmeta живёт час)."""
    start = "$"
    try:
        saved = await pool.fetchval("SELECT last_id FROM rtb_events_cursor WHERE stream = $1", STREAM)
        if saved:
            start = saved
    except Exception:
        logger.exception("events cursor read failed; starting from $")
    try:
        await redis.xgroup_create(STREAM, GROUP, id=start, mkstream=True)
        logger.info("events consumer group created at %s", start)
    except Exception as e:  # BUSYGROUP — группа уже есть
        if "BUSYGROUP" not in str(e):
            raise


async def _save_cursor(pool, last_id: str) -> None:
    try:
        await pool.execute(
            """
            INSERT INTO rtb_events_cursor (stream, last_id, updated_at) VALUES ($1, $2, now())
            ON CONFLICT (stream) DO UPDATE SET last_id = EXCLUDED.last_id, updated_at = now()
            """,
            STREAM, last_id,
        )
    except Exception:
        logger.exception("events cursor save failed (non-fatal)")


async def process_once(redis, pool, *, from_id: str) -> tuple[int, str | None]:
    """Один XREADGROUP + запись + XACK. Возвращает (кол-во событий, последний id)."""
    resp = await redis.xreadgroup(GROUP, CONSUMER, {STREAM: from_id}, count=BATCH, block=BLOCK_MS)
    if not resp:
        return 0, None
    rows: list[dict[str, Any]] = []
    ids: list[str] = []
    for _stream, entries in resp:
        for entry_id, fields in entries:
            ids.append(entry_id)
            norm = normalize(fields)
            if norm is not None:
                rows.append(norm)
    if not ids:
        return 0, None
    await persist_batch(pool, rows)
    await redis.xack(STREAM, GROUP, *ids)
    return len(ids), ids[-1]


async def run_forever(stop: asyncio.Event) -> None:
    """Главный цикл. Импорты внутри, чтобы модуль импортировался в тестах без Redis/PG."""
    from cache import get_redis
    from db import get_pool
    from obs_metrics import metrics as obs_metrics

    redis = get_redis()
    pool = await get_pool()
    await _ensure_group(redis, pool)

    # Сначала — pending этого consumer-а (не подтверждённое до рестарта).
    from_id = "0"
    backoff = 1.0
    since_cursor = 0
    while not stop.is_set():
        try:
            n, last_id = await process_once(redis, pool, from_id=from_id)
            backoff = 1.0
            if from_id == "0" and n == 0:
                from_id = ">"          # pending дочитан — переходим на новые
                continue
            if n:
                obs_metrics.inc("rtb_events_consumed_total", n)
                since_cursor += n
                if since_cursor >= 5000 and last_id:
                    await _save_cursor(pool, last_id)
                    since_cursor = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            obs_metrics.inc("rtb_events_consumer_errors_total")
            logger.exception("events consumer iteration failed; retry in %.0fs", backoff)
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)


_task: asyncio.Task | None = None
_stop = asyncio.Event()


def start_background() -> None:
    """Вызывается из main.py на startup. Отключается RTB_EVENTS_CONSUMER=0
    (например, на инстансе без роли writer или в тестах)."""
    global _task
    if os.getenv("RTB_EVENTS_CONSUMER", "1") == "0":
        logger.info("events consumer disabled by env")
        return
    if not (os.environ.get("REDIS_URL") or os.environ.get("KV_URL")):
        logger.warning("events consumer not started: REDIS_URL missing")
        return
    _stop.clear()
    _task = asyncio.get_event_loop().create_task(run_forever(_stop), name="rtb-events-consumer")


async def stop_background() -> None:
    _stop.set()
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
