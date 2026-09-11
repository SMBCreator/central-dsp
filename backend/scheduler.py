"""Планировщик фоновых задач Central DSP (audit O-01, план C1b).

Отдельный процесс на образе backend (`python scheduler.py`, сервис `scheduler`
в compose.yml). Расписание — таблица `scheduled_jobs` (миграция 053), задачи —
существующие ручки `/cron/*` backend'а, вызываемые по HTTP с
`Authorization: Bearer CRON_SECRET`. Сам код задач не меняется: тот же путь,
что у Vercel Cron / crontab, те же метрики `cron_job_*` в backend.

Модель:
* каждые SCHEDULER_TICK секунд — `SELECT ... FOR UPDATE SKIP LOCKED` по
  просроченным задачам (`enabled AND next_run_at <= now()`): несколько
  экземпляров планировщика не запустят одну задачу дважды;
* захват = lease (`locked_at/locked_by`); пока задача выполняется, воркер
  продлевает `locked_at` каждые HEARTBEAT_SECONDS, поэтому lease протухает
  через LEASE_SECONDS *после последнего heartbeat* — упавший планировщик не
  блокирует задачу дольше ~1.5 минут независимо от её timeout;
* захватывается не больше свободных слотов (concurrency − inflight): задача
  не лежит захваченной в очереди семафора, где её lease мог бы протухнуть и
  привести ко второму параллельному запуску;
* timeout_seconds — общий бюджет запроса (asyncio.wait_for), а не пауза между
  байтами: медленно отдающий тело backend не растягивает задачу;
* misfire: пропущенные запуски схлопываются в один — `next_run_at`
  считается от момента завершения, а не от прошлого `next_run_at`;
* исход пишется в строку (`last_status`, `last_http_status`, `last_error`,
  счётчики) и в метрики scheduler'а (/metrics на SCHEDULER_METRICS_ADDR);
  «сработала ли задача успешно» по-прежнему видно в backend
  (`cron_job_last_success_timestamp_seconds{job=...}`);
* `next_run_at IS NULL` = пересчитать из `schedule` (так API применяет новое
  расписание; «запустить сейчас» = `next_run_at = now()`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg
import httpx

import obs_metrics
from cronexpr import CronError, CronExpr

log = logging.getLogger("scheduler")

# Lease: воркер продлевает locked_at каждые HEARTBEAT_SECONDS; строка без
# продления дольше LEASE_SECONDS считается брошенной и захватывается снова.
# LEASE > 3×HEARTBEAT — один пропущенный heartbeat (кратковременный сбой БД)
# не отдаёт задачу другому экземпляру.
HEARTBEAT_SECONDS = 20
LEASE_SECONDS = 90
# Установка соединения не входит в бюджет медленных задач: недоступный backend
# должен давать unreachable быстро, а не через timeout_seconds.
CONNECT_TIMEOUT_SECONDS = 10.0

# Все известные задачи. Расписание — UTC (МСК = UTC+3). `name` совпадает с
# obs_metrics.CRON_JOBS (label `job` в метриках backend), `path` — с маршрутом
# routers/cron.py; оба инварианта закреплены тестом tests/test_scheduler.py.
DEFAULT_JOBS: tuple[dict, ...] = (
    {"name": "sweep_burl", "path": "/cron/sweep-burl", "schedule": "* * * * *", "timeout_seconds": 50,
     "description": "R-07: выигрыши без burl за BURL_TTL -> win_expired, резерв возвращён."},
    {"name": "reconcile_reserves", "path": "/cron/reconcile-reserves", "schedule": "*/5 * * * *", "timeout_seconds": 240,
     "description": "Сверка Redis avail:adv/avail:camp с БД; чинит дрейф после сбоев Redis."},
    {"name": "audience_sync", "path": "/cron/audience-sync", "schedule": "15 * * * *", "timeout_seconds": 600,
     "description": "R-06: ретаргетинг-аудитории — просроченные, досев audz:* из PG."},
    {"name": "ord_notifications", "path": "/cron/ord-notifications", "schedule": "45 * * * *", "timeout_seconds": 300,
     "description": "B13g: ошибки ЕРИР из ОРД -> статус erir_error у объектов."},
    {"name": "shading", "path": "/cron/shading", "schedule": "0 * * * *", "timeout_seconds": 600,
     "description": "Пересчёт факторов bid-шейдинга по (кампания, SSP)."},
    {"name": "retention", "path": "/cron/retention", "schedule": "30 2 * * *", "timeout_seconds": 3600,
     "description": "Партиции bid_events на неделю вперёд + DROP старше BID_EVENTS_RETENTION_DAYS."},
    {"name": "rules", "path": "/cron/rules", "schedule": "0 6 * * *", "timeout_seconds": 1800,
     "description": "Автоправила кампаний (rules_engine) по вчерашней статистике."},
    {"name": "optimize", "path": "/cron/optimize", "schedule": "0 7 * * *", "timeout_seconds": 1800,
     "description": "Оптимизатор ставок/бюджетов (optimizer)."},
    {"name": "reset_daily", "path": "/cron/reset-daily", "schedule": "30 21 * * *", "timeout_seconds": 600,
     "description": "Housekeeping счётчиков прошлых бизнес-дней (граница дня — Europe/Moscow в ключах, не этот cron)."},
    {"name": "ord_export", "path": "/cron/ord-export", "schedule": "0 3 2 * *", "timeout_seconds": 7200,
     "description": "ОРД: месячная статистика за прошлый месяц (2-е число, 06:00 МСК). Идемпотентно."},
    # «Часть B»: выключены по умолчанию — включает админ, когда фича используется.
    {"name": "fx_refresh", "path": "/cron/fx-refresh", "schedule": "10 0 * * *", "timeout_seconds": 120, "enabled": False,
     "description": "Курс ЦБ РФ (нужен только при RTB_CURRENCY != RUB)."},
    {"name": "ml_train", "path": "/cron/ml-train", "schedule": "0 4 * * *", "timeout_seconds": 3600, "enabled": False,
     "description": "Переобучение pCTR-модели по окну bid_events."},
    {"name": "domain_quality", "path": "/cron/domain-quality", "schedule": "0 5 * * *", "timeout_seconds": 1800, "enabled": False,
     "description": "Скоринг качества доменов для clean-traffic режима."},
)

JOB_COLUMNS = (
    "name, path, schedule, enabled, timeout_seconds, description, next_run_at, locked_at, locked_by, "
    "last_run_at, last_finished_at, last_status, last_http_status, last_duration_ms, last_error, "
    "runs_total, errors_total, created_at, updated_at"
)


def utcnow() -> datetime:
    return datetime.now(UTC)


async def ensure_seed(conn: asyncpg.Connection) -> int:
    """Досеять отсутствующие задачи. Существующие строки не трогаем (правки
    админа важнее дефолтов). -> число добавленных."""
    added = 0
    for job in DEFAULT_JOBS:
        CronExpr.parse(job["schedule"])
        res = await conn.execute(
            """INSERT INTO scheduled_jobs (name, path, schedule, enabled, timeout_seconds, description)
               VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (name) DO NOTHING""",
            job["name"], job["path"], job["schedule"], job.get("enabled", True),
            job["timeout_seconds"], job["description"],
        )
        added += int(res.endswith(" 1"))
    return added


async def fill_next_run(conn: asyncpg.Connection, now: datetime | None = None) -> int:
    """Включённые задачи с next_run_at IS NULL -> ближайший запуск по schedule.
    Невалидное выражение (кто-то поправил БД руками) — задача выключается с
    last_error. Выключенным next_run_at не нужен: API при включении ставит NULL."""
    now = now or utcnow()
    rows = await conn.fetch("SELECT name, schedule FROM scheduled_jobs WHERE enabled AND next_run_at IS NULL")
    for r in rows:
        try:
            nxt = CronExpr.parse(r["schedule"]).next_after(now)
        except CronError as e:
            log.error("job %s: bad schedule %r: %s — disabled", r["name"], r["schedule"], e)
            await conn.execute(
                "UPDATE scheduled_jobs SET enabled = false, last_error = $2, updated_at = now() WHERE name = $1",
                r["name"], f"bad schedule: {e}",
            )
            continue
        await conn.execute(
            "UPDATE scheduled_jobs SET next_run_at = $2, updated_at = now() WHERE name = $1 AND next_run_at IS NULL",
            r["name"], nxt,
        )
    return len(rows)


@dataclass
class Claimed:
    name: str
    path: str
    schedule: str
    timeout_seconds: int


async def claim_due(conn: asyncpg.Connection, worker: str, limit: int = 8) -> list[Claimed]:
    """Захватить просроченные незанятые задачи. Один UPDATE с подзапросом
    FOR UPDATE SKIP LOCKED — параллельные планировщики получают непересекающиеся
    наборы; lease без heartbeat дольше LEASE_SECONDS считается свободным."""
    if limit <= 0:
        return []
    rows = await conn.fetch(
        """
        UPDATE scheduled_jobs j
           SET locked_at = now(), locked_by = $1, last_run_at = now(), updated_at = now()
         WHERE j.name IN (
               SELECT name FROM scheduled_jobs
                WHERE enabled AND next_run_at IS NOT NULL AND next_run_at <= now()
                  AND (locked_at IS NULL OR locked_at < now() - make_interval(secs => $3))
                ORDER BY next_run_at
                FOR UPDATE SKIP LOCKED
                LIMIT $2)
        RETURNING name, path, schedule, timeout_seconds
        """,
        worker, limit, float(LEASE_SECONDS),
    )
    return [Claimed(r["name"], r["path"], r["schedule"], r["timeout_seconds"]) for r in rows]


async def heartbeat(conn: asyncpg.Connection, job: Claimed, worker: str) -> bool:
    """Продлить lease выполняющейся задачи. False = строка уже не наша
    (lease перехвачен после долгой паузы) — исполнитель залогирует потерю."""
    res = await conn.execute(
        "UPDATE scheduled_jobs SET locked_at = now() WHERE name = $1 AND locked_by = $2 AND locked_at IS NOT NULL",
        job.name, worker,
    )
    return res.endswith(" 1")


async def complete(
    conn: asyncpg.Connection, job: Claimed, *, status: str, http_status: int | None,
    duration_ms: int, error: str | None, finished: datetime | None = None, worker: str | None = None,
) -> datetime | None:
    """Записать исход и запланировать следующий запуск. С `worker` строка
    обновляется только если lease всё ещё наш: результат воркера, у которого
    lease перехватили, не затирает состояние нового владельца. -> next_run_at
    (None, если расписание невалидно или строка уже не наша)."""
    finished = finished or utcnow()
    try:
        nxt: datetime | None = CronExpr.parse(job.schedule).next_after(finished)
    except CronError:
        nxt = None  # fill_next_run выключит задачу на следующем тике
    res = await conn.execute(
        """
        UPDATE scheduled_jobs
           SET locked_at = NULL, locked_by = NULL,
               last_finished_at = $2, last_status = $3, last_http_status = $4,
               last_duration_ms = $5, last_error = $6, next_run_at = $7,
               runs_total = runs_total + 1,
               errors_total = errors_total + CASE WHEN $3 = 'ok' THEN 0 ELSE 1 END,
               updated_at = now()
         WHERE name = $1 AND ($8::text IS NULL OR locked_by = $8)
        """,
        job.name, finished, status, http_status, duration_ms, (error or None) and error[:2000], nxt, worker,
    )
    if not res.endswith(" 1"):
        log.error("job %s: lease lost before completion (worker=%s, status=%s) — result not recorded", job.name, worker, status)
        return None
    return nxt


async def run_job(client: httpx.AsyncClient, job: Claimed, base_url: str, secret: str) -> tuple[str, int | None, int, str | None]:
    """-> (status, http_status, duration_ms, error). ok = 2xx; 4xx/5xx = error;
    таймаут = timeout; сетевой сбой = unreachable.

    timeout_seconds — общий бюджет (wait_for); httpx-таймаут только на connect:
    read-таймаут httpx — пауза между байтами, а не длительность запроса."""
    url = base_url.rstrip("/") + job.path
    t0 = time.monotonic()
    http_timeout = httpx.Timeout(None, connect=min(CONNECT_TIMEOUT_SECONDS, job.timeout_seconds))
    try:
        resp = await asyncio.wait_for(
            client.get(url, headers={"Authorization": f"Bearer {secret}"}, timeout=http_timeout),
            timeout=job.timeout_seconds,
        )
    except (httpx.TimeoutException, TimeoutError):
        return "timeout", None, int((time.monotonic() - t0) * 1000), f"no response within {job.timeout_seconds}s"
    except httpx.HTTPError as e:
        return "unreachable", None, int((time.monotonic() - t0) * 1000), f"{type(e).__name__}: {e}"
    ms = int((time.monotonic() - t0) * 1000)
    if 200 <= resp.status_code < 300:
        return "ok", resp.status_code, ms, None
    body = resp.text.strip().replace("\n", " ")
    return "error", resp.status_code, ms, f"HTTP {resp.status_code}: {body[:500]}"


class Scheduler:
    def __init__(
        self, pool: asyncpg.Pool, *, base_url: str, secret: str, worker: str | None = None,
        tick: float = 15.0, client: httpx.AsyncClient | None = None, concurrency: int = 4,
    ) -> None:
        self.pool = pool
        self.base_url = base_url
        self.secret = secret
        self.worker = worker or f"{socket.gethostname()}:{os.getpid()}"
        self.tick = tick
        self.client = client or httpx.AsyncClient()
        self.concurrency = max(1, concurrency)
        self.heartbeat_seconds = float(HEARTBEAT_SECONDS)
        self._stop = asyncio.Event()
        self._inflight: set[asyncio.Task] = set()
        self.last_tick_at: float | None = None

    @property
    def free_slots(self) -> int:
        return self.concurrency - len(self._inflight)

    async def tick_once(self) -> list[Claimed]:
        """Один цикл: досев, пересчёт NULL, захват (не больше свободных слотов),
        запуск. -> захваченные."""
        async with self.pool.acquire() as conn:
            await ensure_seed(conn)
            await fill_next_run(conn)
            claimed = await claim_due(conn, self.worker, limit=self.free_slots)
            overdue = await conn.fetchval(
                "SELECT count(*) FROM scheduled_jobs WHERE enabled AND next_run_at < now() - interval '5 minutes' AND locked_at IS NULL"
            )
        obs_metrics.set_gauge("scheduler_jobs_overdue", float(overdue or 0))
        for job in claimed:
            task = asyncio.create_task(self._execute(job), name=f"job:{job.name}")
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)
        obs_metrics.set_gauge("scheduler_jobs_inflight", float(len(self._inflight)))
        self.last_tick_at = time.time()
        obs_metrics.set_gauge("scheduler_last_tick_timestamp_seconds", self.last_tick_at)
        return claimed

    async def _heartbeat_loop(self, job: Claimed) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                async with self.pool.acquire() as conn:
                    ours = await heartbeat(conn, job, self.worker)
            except Exception:  # noqa: BLE001 — БД моргнула: lease переживёт пропуск (LEASE > 3×HEARTBEAT)
                log.warning("job %s: heartbeat failed", job.name, exc_info=True)
                continue
            if not ours:
                log.error("job %s: lease taken over by another worker while running", job.name)
                obs_metrics.inc("scheduler_lease_lost_total")
                return

    async def _execute(self, job: Claimed) -> None:
        hb = asyncio.create_task(self._heartbeat_loop(job), name=f"heartbeat:{job.name}")
        try:
            status, http_status, ms, error = await run_job(self.client, job, self.base_url, self.secret)
        finally:
            hb.cancel()
        if status != "ok":
            obs_metrics.inc("scheduler_job_failures_total")
        log.log(
            logging.INFO if status == "ok" else logging.ERROR,
            "job %s %s http=%s %dms%s", job.name, status, http_status, ms, f" error={error}" if error else "",
        )
        async with self.pool.acquire() as conn:
            await complete(conn, job, status=status, http_status=http_status, duration_ms=ms, error=error, worker=self.worker)
        obs_metrics.set_gauge("scheduler_jobs_inflight", float(max(0, len(self._inflight) - 1)))

    async def run_forever(self) -> None:
        log.info("scheduler %s started: backend=%s tick=%ss", self.worker, self.base_url, self.tick)
        while not self._stop.is_set():
            try:
                await self.tick_once()
            except Exception:  # БД недоступна и т.п. — не падаем, ждём следующий тик
                log.exception("tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick)
            except TimeoutError:
                pass
        if self._inflight:
            log.info("waiting for %d running job(s)", len(self._inflight))
            await asyncio.gather(*self._inflight, return_exceptions=True)

    def stop(self) -> None:
        self._stop.set()


# ── /metrics и /healthz для Prometheus/Docker (stdlib, отдельный поток) ─────
def _serve_metrics(addr: str, sched: Scheduler, stale_after: float) -> ThreadingHTTPServer:
    host, _, port = addr.rpartition(":")

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/metrics":
                body = obs_metrics.render_prometheus().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            elif self.path == "/healthz":
                fresh = sched.last_tick_at is not None and time.time() - sched.last_tick_at < stale_after
                body = json.dumps({"ok": fresh, "last_tick_at": sched.last_tick_at, "worker": sched.worker}).encode()
                self.send_response(200 if fresh else 503)
                self.send_header("Content-Type", "application/json")
            else:
                body = b"not found"
                self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):  # тихо
            pass

    srv = ThreadingHTTPServer((host or "0.0.0.0", int(port)), H)
    Thread(target=srv.serve_forever, name="metrics", daemon=True).start()
    return srv


def healthcheck(addr: str) -> int:
    """`python scheduler.py --healthcheck` для Docker HEALTHCHECK."""
    import urllib.request

    host, _, port = addr.rpartition(":")
    try:
        with urllib.request.urlopen(f"http://{host or '127.0.0.1'}:{port}/healthz", timeout=3) as r:
            return 0 if r.status == 200 else 1
    except Exception as e:  # noqa: BLE001
        print("healthcheck:", e, file=sys.stderr)
        return 1


def config_problems(env=os.environ) -> list[str]:
    problems = []
    if not env.get("DATABASE_URL"):
        problems.append("DATABASE_URL не задан")
    if not env.get("CRON_SECRET", "").strip():
        problems.append("CRON_SECRET не задан — backend отвергнет /cron/* (401/503)")
    if not env.get("BACKEND_INTERNAL_URL", "").strip():
        problems.append("BACKEND_INTERNAL_URL не задан (в compose: http://backend:8000)")
    return problems


async def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    problems = config_problems()
    if problems:
        for p in problems:
            log.error("config: %s", p)
        return 2
    tick = float(os.environ.get("SCHEDULER_TICK", "15"))
    addr = os.environ.get("SCHEDULER_METRICS_ADDR", ":8001")
    pool = await asyncpg.create_pool(dsn=os.environ["DATABASE_URL"], min_size=1, max_size=4)
    sched = Scheduler(pool, base_url=os.environ["BACKEND_INTERNAL_URL"], secret=os.environ["CRON_SECRET"], tick=tick)
    srv = _serve_metrics(addr, sched, stale_after=max(60.0, 4 * tick))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, sched.stop)
    try:
        await sched.run_forever()
    finally:
        srv.shutdown()
        await sched.client.aclose()
        await pool.close()
    return 0


if __name__ == "__main__":
    if "--healthcheck" in sys.argv[1:]:
        sys.exit(healthcheck(os.environ.get("SCHEDULER_METRICS_ADDR", ":8001").replace("0.0.0.0", "")))
    sys.exit(asyncio.run(_main()))
