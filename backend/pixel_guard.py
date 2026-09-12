"""Защита приёма событий пикселя (/pixel/collect) от дублей и флуда.

Три независимых проверки на Redis, все — одним pipeline:

1. Дедуп: одно и то же событие (pixel, session, type, name, url) в одном
   временном окне пишется один раз. Окно для pageview/event — 2 с
   (повторные sendBeacon при pagehide, двойной вызов скрипта, ретраи
   браузера), для heartbeat — 10 с (сам скрипт шлёт раз в 15 с).
2. Лимит на сессию: не более PIXEL_SESSION_RPM событий в минуту — защита
   от зациклившегося cdsp.track() и накрутки конверсий.
3. Лимит на IP: не более PIXEL_IP_RPM событий в минуту со всех сессий —
   ограничивает ботов, генерирующих новые sid на каждый запрос.

Fail-open: если Redis недоступен, событие принимается (лучше лишняя строка
в pixel_events, чем потерянная конверсия), но это логируется.
"""
from __future__ import annotations

import hashlib
import logging
import os

from redis.exceptions import RedisError

from cache import get_redis

log = logging.getLogger("pixel_guard")

SESSION_RPM = int(os.environ.get("PIXEL_SESSION_RPM", "120"))
IP_RPM = int(os.environ.get("PIXEL_IP_RPM", "600"))
DEDUP_TTL = {"pageview": 2, "event": 2, "heartbeat": 10}

# Причины отказа — используются в ответе (заголовок X-Pixel-Drop) и метриках.
DROP_DUPLICATE = "duplicate"
DROP_SESSION_LIMIT = "session_limit"
DROP_IP_LIMIT = "ip_limit"


def _fp(*parts: str | None) -> str:
    h = hashlib.sha256("\x1f".join(p or "" for p in parts).encode("utf-8", "replace")).hexdigest()
    return h[:32]


async def should_accept(
    *,
    pixel_id: str,
    session_id: str,
    event_type: str,
    event_name: str | None,
    url: str | None,
    client_ip: str | None,
) -> str | None:
    """Вернуть None, если событие можно принять, иначе — причину отказа."""
    dedup_key = f"px:dd:{pixel_id}:{_fp(session_id, event_type, event_name, url)}"
    sess_key = f"px:rl:s:{pixel_id}:{session_id}"
    ip_key = f"px:rl:ip:{_fp(client_ip)}" if client_ip else None

    try:
        r = get_redis()
        pipe = r.pipeline(transaction=False)
        pipe.set(dedup_key, "1", nx=True, ex=DEDUP_TTL.get(event_type, 2))
        pipe.incr(sess_key)
        pipe.expire(sess_key, 60, nx=True)
        if ip_key:
            pipe.incr(ip_key)
            pipe.expire(ip_key, 60, nx=True)
        res = await pipe.execute()
    except (RedisError, RuntimeError, OSError) as exc:
        # RuntimeError — REDIS_URL не настроен (dev/тесты без Redis).
        log.warning("pixel_guard: redis unavailable, fail-open: %s", exc)
        return None

    is_new = bool(res[0])
    sess_count = int(res[1] or 0)
    ip_count = int(res[3] or 0) if ip_key else 0

    if ip_key and ip_count > IP_RPM:
        return DROP_IP_LIMIT
    if sess_count > SESSION_RPM:
        return DROP_SESSION_LIMIT
    if not is_new:
        return DROP_DUPLICATE
    return None
