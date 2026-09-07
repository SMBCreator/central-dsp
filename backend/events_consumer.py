"""Consumer событий Go-биддера: Redis Stream rtb:events -> bid_events + stats_hourly.

Почему это нужно. Go-биддер не ходит в Postgres (латентность торгов), поэтому
события «bid» он складывает в Redis Stream (bidder-go/internal/redisx.EmitEvent).
Без переноса этих событий в БД у кабинета нет знаменателя для win-rate и eCPM,
а отчёты «по площадкам» пусты. Этот модуль — единственный писатель строк
event_type IN ('bid','loss') для Go-трафика.

Схема надёжности (audit E-03):
  * consumer group «backend» — at-least-once на стороне Redis; XACK только после
    успешного COMMIT;
  * exactly-once на стороне БД: stream id каждого события пишется в
    rtb_events_inbox (041) в той же транзакции, что bid_events/stats_hourly;
    ON CONFLICT DO NOTHING — уже вставленные id (crash между COMMIT и XACK)
    молча пропускаются, агрегат bids считается только по новым строкам;
  * после любой ошибки цикл возвращается к чтению pending («0») и не берёт
    новые события, пока не запишет старые — иначе упавший батч висит в PEL;
  * XAUTOCLAIM: pending чужих consumer-ов (старый HOSTNAME контейнера,
    упавшая реплика) старше CLAIM_IDLE_MS забираем себе;
  * poison event: если батч не записывается MAX_DELIVERIES раз, событие
    уходит в rtb:events:dead и подтверждается — одно битое событие не должно
    останавливать весь поток;
  * bootstrap группы — с «0» (всё, что есть в стриме) или с курсора из БД;
    курсор пишется в той же транзакции, что и батч;
  * gauges rtb_events_stream_length / pending / lag — для алерта на отставание
    и потерю по MAXLEN (стрим на стороне Go ограничен ~200k событий).

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
DEAD_STREAM = "rtb:events:dead"
GROUP = "backend"
CONSUMER = os.getenv("HOSTNAME", "backend-1")
BATCH = int(os.getenv("RTB_EVENTS_BATCH", "500"))
BLOCK_MS = int(os.getenv("RTB_EVENTS_BLOCK_MS", "1000"))
# Через сколько мс pending чужого consumer-а считается брошенным.
CLAIM_IDLE_MS = int(os.getenv("RTB_EVENTS_CLAIM_IDLE_MS", "60000"))
# Сколько раз событие может быть доставлено без успешной записи, прежде чем
# уйдёт в dead-stream.
MAX_DELIVERIES = int(os.getenv("RTB_EVENTS_MAX_DELIVERIES", "5"))
# Как часто (сек) обновлять gauges стрима.
GAUGE_EVERY_S = 5.0

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


_INBOX_SQL = """
INSERT INTO rtb_events_inbox (stream_id)
SELECT unnest($1::text[])
ON CONFLICT (stream_id) DO NOTHING
RETURNING stream_id
"""

_CURSOR_SQL = """
INSERT INTO rtb_events_cursor (stream, last_id, updated_at) VALUES ($1, $2, now())
ON CONFLICT (stream) DO UPDATE SET last_id = EXCLUDED.last_id, updated_at = now()
"""


async def persist_batch(pool, items: list[tuple[str, dict[str, Any]]], last_id: str | None = None) -> int:
    """Записать батч (stream_id, row) одной транзакцией.

    Inbox-фильтр: вставляем stream_id в rtb_events_inbox ON CONFLICT DO NOTHING
    и работаем только с теми, что реально вставились — повтор батча после
    crash-between-COMMIT-and-XACK не задваивает ни bid_events, ни bids.
    Курсор пишется здесь же, атомарно с данными. Возвращает число новых событий.
    """
    if not items and not last_id:
        return 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            fresh: set[str] = set()
            if items:
                inserted = await conn.fetch(_INBOX_SQL, [sid for sid, _ in items])
                fresh = {r["stream_id"] for r in inserted}
            rows = [row for sid, row in items if sid in fresh]
            if rows:
                await conn.executemany(_INSERT_SQL, [tuple(r[c] for c in _INSERT_COLS) for r in rows])
                bumps = aggregate_bids(rows)
                if bumps:
                    await conn.executemany(_BUMP_BIDS_SQL, bumps)
            if last_id:
                await conn.execute(_CURSOR_SQL, STREAM, last_id)
            return len(rows)


async def _ensure_group(redis, pool) -> None:
    """Создать consumer group (идемпотентно).

    Старт: курсор из БД (Redis пересоздан, группа потеряна) либо «0» — всё, что
    сейчас лежит в стриме. Раньше было «$» и уже записанные Go-биддером события
    молча пропускались на первом запуске.
    """
    start = "0"
    try:
        saved = await pool.fetchval("SELECT last_id FROM rtb_events_cursor WHERE stream = $1", STREAM)
        if saved:
            start = saved
    except Exception:
        logger.exception("events cursor read failed; starting from 0")
    try:
        await redis.xgroup_create(STREAM, GROUP, id=start, mkstream=True)
        logger.info("events consumer group created at %s", start)
    except Exception as e:  # BUSYGROUP — группа уже есть
        if "BUSYGROUP" not in str(e):
            raise


def _split_entries(entries) -> tuple[list[str], list[tuple[str, dict[str, Any]]]]:
    ids: list[str] = []
    items: list[tuple[str, dict[str, Any]]] = []
    for entry_id, fields in entries:
        ids.append(entry_id)
        norm = normalize(fields)
        if norm is not None:
            items.append((entry_id, norm))
    return ids, items


async def _quarantine(redis, entries) -> int:
    """Poison events: переложить в dead-stream и подтвердить, чтобы не блокировать
    остальной поток. Возвращает число отправленных в карантин."""
    ids: list[str] = []
    for entry_id, fields in entries:
        payload = {str(k): str(v) for k, v in dict(fields).items()}
        payload["_src_id"] = entry_id
        payload["_consumer"] = CONSUMER
        await redis.xadd(DEAD_STREAM, payload, maxlen=10_000, approximate=True)
        ids.append(entry_id)
    if ids:
        await redis.xack(STREAM, GROUP, *ids)
        logger.error("events consumer: %d poison event(s) moved to %s: %s", len(ids), DEAD_STREAM, ids[:5])
    return len(ids)


async def _poisoned_ids(redis, ids: list[str]) -> set[str]:
    """Из pending-записей выбрать те, что доставлялись >= MAX_DELIVERIES раз."""
    if not ids:
        return set()
    try:
        pend = await redis.xpending_range(STREAM, GROUP, min=ids[0], max=ids[-1], count=len(ids))
    except Exception:
        logger.exception("xpending_range failed; skipping poison check")
        return set()
    bad: set[str] = set()
    for p in pend:
        if int(p.get("times_delivered", 0)) >= MAX_DELIVERIES:
            bad.add(p["message_id"])
    return bad


async def _handle_entries(redis, pool, entries, *, poison_check: bool) -> tuple[int, str | None]:
    """Общий путь для pending/claimed/new: (опц.) карантин, запись, XACK."""
    entries = list(entries)
    if not entries:
        return 0, None
    if poison_check:
        bad = await _poisoned_ids(redis, [e[0] for e in entries])
        if bad:
            await _quarantine(redis, [e for e in entries if e[0] in bad])
            entries = [e for e in entries if e[0] not in bad]
            if not entries:
                return 0, None
    ids, items = _split_entries(entries)
    await persist_batch(pool, items, last_id=ids[-1])
    await redis.xack(STREAM, GROUP, *ids)
    return len(ids), ids[-1]


async def process_once(redis, pool, *, from_id: str) -> tuple[int, str | None]:
    """Один XREADGROUP + запись + XACK. Возвращает (кол-во событий, последний id).

    from_id «0» — pending этого consumer-а (перечитывается после ошибки и на
    старте, с проверкой на poison); «>» — новые события.
    """
    resp = await redis.xreadgroup(GROUP, CONSUMER, {STREAM: from_id}, count=BATCH, block=BLOCK_MS)
    if not resp:
        return 0, None
    entries = [e for _stream, es in resp for e in es]
    return await _handle_entries(redis, pool, entries, poison_check=(from_id != ">"))


async def claim_stale(redis, pool) -> int:
    """XAUTOCLAIM: забрать pending чужих consumer-ов, простаивающие > CLAIM_IDLE_MS
    (контейнер с другим HOSTNAME, упавшая реплика). Возвращает число обработанных."""
    total = 0
    start = "0-0"
    while True:
        try:
            resp = await redis.xautoclaim(
                STREAM, GROUP, CONSUMER, min_idle_time=CLAIM_IDLE_MS, start_id=start, count=BATCH,
            )
        except Exception as e:
            if "unknown command" in str(e).lower():   # Redis < 6.2
                return 0
            raise
        next_start, entries = resp[0], resp[1]
        # Записи, удалённые из стрима (MAXLEN), приходят как (id, None) — это
        # потерянные события: подтверждаем, чтобы не висели в PEL, и считаем.
        lost = [e[0] for e in entries if e[1] is None]
        live = [e for e in entries if e[1] is not None]
        if lost:
            await redis.xack(STREAM, GROUP, *lost)
            logger.error("events consumer: %d pending entries trimmed from stream (data loss)", len(lost))
        n, _ = await _handle_entries(redis, pool, live, poison_check=True)
        total += n
        if next_start == "0-0" or not entries:
            return total
        start = next_start


async def refresh_gauges(redis, obs_metrics) -> None:
    """Длина стрима, размер PEL группы и lag (сколько событий ещё не доставлено)."""
    try:
        length = await redis.xlen(STREAM)
        obs_metrics.set_gauge("rtb_events_stream_length", length)
        for g in await redis.xinfo_groups(STREAM):
            if g.get("name") == GROUP:
                obs_metrics.set_gauge("rtb_events_pending", int(g.get("pending", 0)))
                lag = g.get("lag")
                if lag is not None:
                    obs_metrics.set_gauge("rtb_events_lag", int(lag))
                break
    except Exception:
        logger.debug("events consumer gauges refresh failed", exc_info=True)


async def run_forever(stop: asyncio.Event) -> None:
    """Главный цикл. Импорты внутри, чтобы модуль импортировался в тестах без Redis/PG."""
    from cache import get_redis
    from db import get_pool
    from obs_metrics import metrics as obs_metrics

    redis = get_redis()
    pool = await get_pool()

    backoff = 1.0
    while not stop.is_set():
        try:
            await _ensure_group(redis, pool)
            break
        except asyncio.CancelledError:
            raise
        except Exception:
            obs_metrics.inc("rtb_events_consumer_errors_total")
            logger.exception("events consumer group bootstrap failed; retry in %.0fs", backoff)
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)

    # «0» — сначала свой pending (после рестарта и после каждой ошибки), затем «>».
    from_id = "0"
    backoff = 1.0
    last_claim = 0.0
    last_gauge = 0.0
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        try:
            now = loop.time()
            if now - last_claim >= CLAIM_IDLE_MS / 1000:
                claimed = await claim_stale(redis, pool)
                if claimed:
                    obs_metrics.inc("rtb_events_consumed_total", claimed)
                    obs_metrics.inc("rtb_events_claimed_total", claimed)
                last_claim = now
            if now - last_gauge >= GAUGE_EVERY_S:
                await refresh_gauges(redis, obs_metrics)
                last_gauge = now

            n, _last_id = await process_once(redis, pool, from_id=from_id)
            backoff = 1.0
            if from_id == "0" and n == 0:
                from_id = ">"          # pending дочитан — переходим на новые
                continue
            if n:
                obs_metrics.inc("rtb_events_consumed_total", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            obs_metrics.inc("rtb_events_consumer_errors_total")
            logger.exception("events consumer iteration failed; retry in %.0fs", backoff)
            # Упавший батч висит в PEL — следующая итерация перечитает его с «0»,
            # новых событий не берём до успешной записи старых.
            from_id = "0"
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
