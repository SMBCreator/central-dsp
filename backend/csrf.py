"""Origin/Referer-гейт для state-changing запросов (audit S-01, CSRF).

Сессия живёт в cookie `cdsp_session`. Браузер прикладывает её к любому
POST/PUT/PATCH/DELETE — в том числе к форме на чужом сайте. Без проверки
источника запроса злоумышленник заставляет залогиненного менеджера
поставить кампанию на паузу, сменить click_url или пополнить баланс «от
своего имени» — достаточно одной невидимой формы.

Правило: для мутирующих методов источник запроса (заголовок `Origin`,
иначе origin из `Referer`) должен быть одним из наших:
  * `PUBLIC_ORIGINS` — явный список публичных origin'ов через запятую
    (`https://dsp.example.com,https://admin.example.com`);
  * либо совпадать с origin самого запроса (`X-Forwarded-Proto` +
    `X-Forwarded-Host`/`Host`) — за Caddy/Vercel это публичный хост, так что
    same-origin запрос из нашего же UI проходит и без PUBLIC_ORIGINS.

Запрос без Origin и без Referer — не браузерный (curl, скрипты, тесты):
у него нет cookie жертвы, CSRF к нему неприменим — пропускаем. Браузер на
POST шлёт `Origin` всегда (для cross-site — обязательно), `Origin: null`
(sandbox-iframe, редирект с data:) — отклоняем.

Исключения — ручки со своей аутентификацией, куда браузер пользователя
не ходит с cookie: OpenRTB (`/rtb/*`), фид биддера (`/rtb-feed/*`),
пиксель (`/pixel/collect`, публичный, без сессии), cron (`CRON_SECRET`).
Управление пикселями (`/pixels/*`) — обычная cookie-ручка, защищена.

Вне production без PUBLIC_ORIGINS гейт только логирует (dev-стенд, где
UI и API на разных портах); в production он всегда включён.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

log = logging.getLogger("csrf")

STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Пути со своей auth (токен/подпись/секрет), не cookie-сессия.
EXEMPT_PREFIXES: tuple[str, ...] = (
    "/rtb",
    "/rtb-feed",
    "/pixel/collect",
    "/cron",
    "/alerts/webhook",  # Alertmanager, Bearer ALERT_WEBHOOK_SECRET
    "/health",
    "/metrics",
)

REJECT_DETAIL = "Запрос отклонён: источник (Origin) не принадлежит этому сайту"


def is_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in EXEMPT_PREFIXES)


def normalize_origin(value: str | None) -> str | None:
    """`https://Host:443/` -> `https://host:443`; мусор -> None."""
    if not value:
        return None
    v = value.strip().rstrip("/")
    if not v or v.lower() == "null":
        return None
    parts = urlsplit(v)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def allowed_origins(env: Mapping[str, str] | None = None) -> frozenset[str]:
    env = os.environ if env is None else env
    raw = env.get("PUBLIC_ORIGINS", "")
    out = set()
    for item in raw.split(","):
        n = normalize_origin(item)
        if n:
            out.add(n)
    return frozenset(out)


def request_source(headers: Mapping[str, str]) -> tuple[str | None, bool]:
    """(origin источника | None, был ли заголовок).

    `Origin` приоритетнее `Referer`. Возвращает (None, True), если заголовок
    есть, но невалиден/`null` — такое отклоняем, это не «нет источника».
    """
    origin = headers.get("origin")
    if origin is not None:
        return normalize_origin(origin), True
    referer = headers.get("referer")
    if referer is not None:
        return normalize_origin(referer), True
    return None, False


def self_origin(headers: Mapping[str, str], *, production: bool) -> str | None:
    """Origin самого запроса, как его видит браузер: за reverse-proxy —
    X-Forwarded-Host/Proto, иначе Host."""
    host = headers.get("x-forwarded-host") or headers.get("host")
    if not host:
        return None
    host = host.split(",")[0].strip()
    proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        proto = "https" if production else "http"
    return normalize_origin(f"{proto}://{host}")


def verdict(
    method: str,
    path: str,
    headers: Mapping[str, str],
    *,
    allowed: frozenset[str],
    production: bool,
) -> str | None:
    """None — пропустить; строка — причина отказа (403)."""
    if method.upper() not in STATE_CHANGING or is_exempt(path):
        return None
    source, present = request_source(headers)
    if not present:
        return None  # не браузер: cookie жертвы у него нет
    if source is None:
        return "origin header present but invalid or null"
    if source in allowed:
        return None
    own = self_origin(headers, production=production)
    if own and source == own:
        return None
    if headers.get("sec-fetch-site", "").lower() in ("same-origin", "none") and not allowed:
        # Браузер сам утверждает same-origin, а нам нечего сверять — верим
        # (за proxy без X-Forwarded-Host origin по Host не восстановить).
        return None
    return f"origin {source} not in allowed set"


class OriginGuardMiddleware:
    """Pure-ASGI: не буферизует тело, не трогает не-HTTP scope."""

    def __init__(self, app, *, production: bool | None = None, env: Mapping[str, str] | None = None):
        self.app = app
        self._env = env
        self._production = production

    def _production_now(self) -> bool:
        if self._production is not None:
            return self._production
        env = os.environ if self._env is None else self._env
        return env.get("APP_ENV", "").strip().lower() in ("production", "prod")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        if method not in STATE_CHANGING:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        production = self._production_now()
        allowed = allowed_origins(self._env)
        reason = verdict(method, scope.get("path", ""), headers, allowed=allowed, production=production)
        if reason is None:
            await self.app(scope, receive, send)
            return
        enforce = production or bool(allowed)
        log.warning(
            "csrf %s %s %s: %s", "reject" if enforce else "would-reject (dev, PUBLIC_ORIGINS unset)",
            method, scope.get("path", ""), reason,
        )
        if not enforce:
            await self.app(scope, receive, send)
            return
        try:
            import obs_metrics

            obs_metrics.inc("csrf_rejected_total")
        except Exception:  # noqa: BLE001 — метрика не должна ломать отказ
            pass
        response = JSONResponse(status_code=403, content={"detail": REJECT_DETAIL})
        await response(scope, receive, send)


def cookie_samesite(env: Mapping[str, str] | None = None) -> str:
    """SameSite для сессионной cookie.

    `lax` — default в production: cookie не уходит с cross-site POST, это
    второй слой защиты от CSRF поверх Origin-гейта. `none` нужен только там,
    где UI живёт в чужом iframe (превью v0) — включается явно
    `AUTH_COOKIE_SAMESITE=none`; вне production это и default, чтобы не
    ломать локальную разработку и превью.
    """
    env = os.environ if env is None else env
    raw = env.get("AUTH_COOKIE_SAMESITE", "").strip().lower()
    if raw in ("lax", "strict", "none"):
        return raw
    production = env.get("APP_ENV", "").strip().lower() in ("production", "prod")
    return "lax" if production else "none"
