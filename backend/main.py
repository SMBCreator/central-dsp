"""CentralDSP backend: management API + OpenRTB 2.5 bidder.

Routes are defined WITHOUT the /api prefix. The new Vercel `services`
model forwards the ORIGINAL path (/api/...), so an ASGI middleware at
the bottom of this file strips the prefix before routing.
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
import os

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "uploads"))
from fastapi.responses import JSONResponse

from routers.advertisers import router as advertisers_router
from routers.alerts import router as alerts_router
from routers.bidder_feed import router as bidder_feed_router
from routers.auth_router import router as auth_router
from routers.billing import router as billing_router
from routers.campaigns import router as campaigns_router
from routers.creatives import router as creatives_router
from routers.cron import router as cron_router
from routers.groups import router as groups_router
from routers.settings import router as settings_router
from routers.geo import router as geo_router
from routers.notifications import router as notifications_router
from routers.moderation import router as moderation_router
from routers.audiences_router import router as audiences_router
from routers.audit_router import router as audit_router
from routers.line_items import router as line_items_router
from routers.pixel import router as pixel_router
from routers.rtb import router as rtb_router
from routers.ord_router import router as ord_router
from routers.users_router import router as users_router
from routers.rules import router as rules_router
from routers.scoring import router as scoring_router
from routers.scheduler_router import router as scheduler_router
from routers.share import router as share_router
from routers.simulator import router as simulator_router
from routers.stats import router as stats_router
from routers.supply_partners import router as supply_partners_router
from routers.id_sync import router as id_sync_router

import validation_i18n
from audit import AuditMiddleware
from csrf import OriginGuardMiddleware
from http_hardening import NoStoreMiddleware
from startup_checks import is_production as _is_production
from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Фоновые задачи процесса. Сейчас одна: consumer событий Go-биддера
    (rtb:events -> bid_events/stats_hourly). Без него у Go-трафика нет
    бидов в статистике => win-rate/eCPM в кабинете пустые."""
    import asyncio
    import logging

    import events_consumer
    import readiness
    import startup_checks
    from routers.cron import seed_gauges_from_db

    # APP_ENV=production + пустой/плейсхолдерный секрет => RuntimeError,
    # процесс не поднимается (чеклист 6.1 / 6.11).
    startup_checks.assert_production_config()
    # Redis хранит деньги торгов: eviction-политика в production фатальна (M-03).
    try:
        from cache import get_redis

        await asyncio.wait_for(
            startup_checks.check_redis_durability(get_redis(), production=startup_checks.is_production()),
            timeout=5,
        )
    except RuntimeError:
        raise
    except Exception as e:  # noqa: BLE001 — недоступный Redis не должен блокировать старт API
        logging.getLogger("startup").warning("redis durability check skipped: %s", e)
    # Production без ключа ОРД => RuntimeError: mock-erid на боевом трафике запрещены (L-01).
    try:
        await asyncio.wait_for(
            startup_checks.check_ord_configured(production=startup_checks.is_production()),
            timeout=5,
        )
    except RuntimeError:
        raise
    except Exception as e:  # noqa: BLE001 — БД недоступна: get_ord_settings уже упал в env-ветку
        logging.getLogger("startup").warning("ord config check skipped: %s", e)
    try:
        await asyncio.wait_for(seed_gauges_from_db(), timeout=3)
    except Exception as e:  # noqa: BLE001 — БД может быть недоступна; метрика просто absent
        logging.getLogger("startup").warning("seed_gauges_from_db skipped: %s", e)
    # C4b: без SMTP письма копятся в notification_outbox и никуда не уходят —
    # алерты суперадмину и бизнес-уведомления молчат. Не фатально, но громко.
    import alerting
    import notify_outbox

    alerting.seed_watchdog()
    if not notify_outbox.is_configured():
        logging.getLogger("startup").warning(
            "SMTP_HOST/SMTP_FROM not set: notification emails (tech alerts, business events) "
            "will stay queued in notification_outbox until SMTP is configured"
        )
    events_consumer.start_background()
    readiness.start_background()
    try:
        yield
    finally:
        # Graceful shutdown (C6): сначала фоновые задачи, затем клиентские пулы,
        # чтобы consumer не пытался писать в уже закрытый пул.
        await readiness.stop_background()
        await events_consumer.stop_background()
        from cache import close_redis
        from db import close_pool

        await close_redis()
        await close_pool()


# В production схема API (/docs, /redoc, /openapi.json) не публикуется: это
# полная карта ручек для постороннего. Локально и на стенде — как раньше.
_API_DOCS = not _is_production()

app = FastAPI(
    title="CentralDSP API",
    version="1.0.0",
    description="Demand-Side Platform: management API + OpenRTB 2.5 bidder",
    lifespan=_lifespan,
    docs_url="/docs" if _API_DOCS else None,
    redoc_url="/redoc" if _API_DOCS else None,
    openapi_url="/openapi.json" if _API_DOCS else None,
)

validation_i18n.install(app)

app.add_middleware(AuditMiddleware)
# CSRF (audit S-01): мутирующий запрос с чужим Origin -> 403 до роутера и до
# audit-лога. Снаружи AuditMiddleware, чтобы отказ не писался как действие.
app.add_middleware(OriginGuardMiddleware)

app.include_router(auth_router)
app.include_router(advertisers_router)
app.include_router(campaigns_router)
app.include_router(line_items_router)
app.include_router(creatives_router)
app.include_router(supply_partners_router)
app.include_router(rules_router)
app.include_router(scoring_router)
app.include_router(stats_router)
app.include_router(audiences_router)
app.include_router(bidder_feed_router)
app.include_router(rtb_router)
app.include_router(ord_router)
app.include_router(users_router)
app.include_router(simulator_router)
app.include_router(cron_router)
app.include_router(scheduler_router)
app.include_router(billing_router)
app.include_router(share_router)
app.include_router(pixel_router)
app.include_router(groups_router)
app.include_router(settings_router)
app.include_router(geo_router)
app.include_router(notifications_router)
app.include_router(alerts_router)
app.include_router(moderation_router)
app.include_router(audit_router)
app.include_router(id_sync_router)

os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


_VIEWER_WRITE_ALLOWED = {"/auth/logout", "/auth/login"}


@app.middleware("http")
async def viewer_readonly_guard(request: Request, call_next):
    """Guest (viewer) accounts are read-only: block all mutating requests."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path not in _VIEWER_WRITE_ALLOWED:
        token = request.cookies.get("cdsp_session")
        if token:
            from db import get_pool
            pool = await get_pool()
            role = await pool.fetchval(
                "SELECT u.role FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = $1",
                token,
            )
            if role == "viewer":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Гостевой доступ доступен только для просмотра"},
                )
    return await call_next(request)


# --- Глобальный rate limiting (GAP: все ручки, не только логин) ---------------
# Redis fixed-window. Ключ: user_id (по сессионной cookie) либо client IP.
# RTB hot path и health исключены: у бидера свой QPS-контроль per-SSP.
_RL_EXEMPT_PREFIXES = ("/rtb", "/rtb-feed", "/health", "/ready", "/metrics", "/uploads", "/pixel")
_RL_LIMIT = int(os.getenv("API_RATE_LIMIT", "300"))          # запросов
_RL_WINDOW = int(os.getenv("API_RATE_WINDOW", "60"))         # за окно, сек


@app.middleware("http")
async def global_rate_limiter(request: Request, call_next):
    path = request.url.path
    if not any(path.startswith(p) for p in _RL_EXEMPT_PREFIXES):
        key = request.cookies.get("cdsp_session")
        if not key:
            fwd = request.headers.get("x-forwarded-for")
            key = (fwd.split(",")[0].strip() if fwd else None) or (
                request.client.host if request.client else "unknown"
            )
        from cache import rate_limit
        ok = await rate_limit("api", key, limit=_RL_LIMIT, window_seconds=_RL_WINDOW)
        if not ok:
            return JSONResponse(
                status_code=429,
                content={"detail": "Слишком много запросов — попробуйте через минуту"},
                headers={"Retry-After": str(_RL_WINDOW)},
            )
    return await call_next(request)


@app.middleware("http")
async def http_status_class_metrics(request: Request, call_next):
    """Счётчик http_responses_total{class} для алерта Backend5xxShare (D8).
    Регистрируется последним => внешний слой: видит и 429 лимитера, и 403
    viewer-guard'а. Необработанное исключение станет 500 в ServerErrorMiddleware —
    считаем его как 5xx здесь и пробрасываем дальше."""
    import obs_metrics

    if request.url.path == "/metrics":
        return await call_next(request)
    try:
        response = await call_next(request)
    except Exception:
        obs_metrics.inc_http_status(500)
        raise
    obs_metrics.inc_http_status(response.status_code)
    return response


@app.get("/health")
async def health():
    """Liveness (audit O-05): процесс и HTTP-сервер отвечают. Не зависит от
    PostgreSQL/Redis/consumer — только его опрашивает Docker HEALTHCHECK."""
    return {"status": "ok", "service": "centraldsp"}


@app.get("/ready")
async def ready():
    """Readiness: PostgreSQL ping, Redis ping, consumer rtb:events жив и не
    отстаёт. 503 + коды состояний по каждой проверке — для deploy gate
    (preprod_check), readiness-мониторинга и балансировщика. Без адресов,
    DSN и текста исключений в теле (они в логах, см. readiness.py)."""
    import readiness

    report = await readiness.evaluate()
    return JSONResponse(status_code=200 if report.ready else 503, content=report.body())


@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint():
    """Prometheus text exposition (GAP-011). Процессные счётчики, без PII/ID в лейблах."""
    from fastapi.responses import PlainTextResponse

    import obs_metrics

    return PlainTextResponse(obs_metrics.render_prometheus(), media_type="text/plain; version=0.0.4")


class ApiPrefixStripper:
    """Срезает /api из пути запроса (новая модель Vercel services
    передаёт оригинальный путь, роуты объявлены без префикса).
    Локальный запуск без префикса продолжает работать как раньше."""

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == "/api" or path.startswith("/api/"):
                scope = dict(scope)
                new_path = path[4:] or "/"
                scope["path"] = new_path
                scope["raw_path"] = new_path.encode()
        await self.asgi_app(scope, receive, send)


# Cache-Control: no-store для персональных ответов API (C6). Снаружи всех
# http-мидлварей, но внутри ApiPrefixStripper: exempt-список сверяется с путём
# уже без префикса /api.
app.add_middleware(NoStoreMiddleware)
app.add_middleware(ApiPrefixStripper)
