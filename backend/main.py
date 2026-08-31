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
from routers.line_items import router as line_items_router
from routers.pixel import router as pixel_router
from routers.rtb import router as rtb_router
from routers.ord_router import router as ord_router
from routers.users_router import router as users_router
from routers.rules import router as rules_router
from routers.share import router as share_router
from routers.simulator import router as simulator_router
from routers.stats import router as stats_router
from routers.supply_partners import router as supply_partners_router

from audit import AuditMiddleware

app = FastAPI(
    title="CentralDSP API",
    version="1.0.0",
    description="Demand-Side Platform: management API + OpenRTB 2.5 bidder",
)

app.add_middleware(AuditMiddleware)

app.include_router(auth_router)
app.include_router(advertisers_router)
app.include_router(campaigns_router)
app.include_router(line_items_router)
app.include_router(creatives_router)
app.include_router(supply_partners_router)
app.include_router(rules_router)
app.include_router(stats_router)
app.include_router(audiences_router)
app.include_router(bidder_feed_router)
app.include_router(rtb_router)
app.include_router(ord_router)
app.include_router(users_router)
app.include_router(simulator_router)
app.include_router(cron_router)
app.include_router(billing_router)
app.include_router(share_router)
app.include_router(pixel_router)
app.include_router(groups_router)
app.include_router(settings_router)
app.include_router(geo_router)
app.include_router(notifications_router)
app.include_router(moderation_router)

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
_RL_EXEMPT_PREFIXES = ("/rtb", "/rtb-feed", "/health", "/metrics", "/uploads", "/pixel")
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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "centraldsp"}


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


app.add_middleware(ApiPrefixStripper)
