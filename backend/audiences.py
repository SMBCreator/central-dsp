"""Retargeting audiences: pixel-driven segments with Redis-backed membership.

Data flow:
  1. Bid hot path stores a short-lived bridge  clidmap:{bid_id} -> device_id
     (fire-and-forget; the click redirect appends cdsp_clid={bid_id} to the
     landing URL, so the pixel reports events with clid = bid_id).
  2. Pixel /collect resolves clid -> device_id and matches active segments of
     that pixel; membership goes to Postgres (truth) + Redis SET (hot path).
  3. The bidder checks targeting audience_include / audience_exclude with
     SISMEMBER — fail-open on any Redis error.

Redis keys:
  clidmap:{bid_id}      device_id                    TTL 7d
  aud:{segment_id}      SET of device_id             TTL = segment ttl_days
"""
import logging
from datetime import datetime, timedelta, timezone

from cache import get_redis

logger = logging.getLogger("audiences")

CLIDMAP_TTL = 7 * 24 * 3600


async def store_clid_bridge(bid_id: str, device_id: str) -> None:
    """Best-effort clid->device bridge. Never raises."""
    if not bid_id or not device_id:
        return
    try:
        r = get_redis()
        await r.set(f"clidmap:{bid_id}", device_id, ex=CLIDMAP_TTL)
    except Exception:  # noqa: BLE001 - hot path must not fail
        pass


async def resolve_clid(clid: str) -> str | None:
    try:
        r = get_redis()
        v = await r.get(f"clidmap:{clid}")
        return v.decode() if isinstance(v, bytes) else v
    except Exception:  # noqa: BLE001
        return None


def _segment_matches(seg, event_type: str, event_name: str | None, url: str | None) -> bool:
    rt, rv = seg["rule_type"], seg["rule_value"]
    if rt == "all_visitors":
        return event_type == "pageview"
    if rt == "url_contains":
        return bool(event_type == "pageview" and url and rv and rv in url)
    if rt == "event":
        return bool(event_type == "event" and event_name and event_name == rv)
    # goal segments are filled by the goal-conversion path in pixel.py
    return False


async def add_member(pool, segment_id: str, ttl_days: int, device_id: str) -> None:
    """Persist membership + mirror to Redis. Best-effort on Redis."""
    expires = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    await pool.execute(
        """INSERT INTO audience_members (segment_id, device_id, expires_at)
           VALUES ($1, $2, $3)
           ON CONFLICT (segment_id, device_id) DO UPDATE SET expires_at = EXCLUDED.expires_at""",
        segment_id, device_id, expires,
    )
    try:
        r = get_redis()
        key = f"aud:{segment_id}"
        await r.sadd(key, device_id)
        await r.expire(key, ttl_days * 24 * 3600)
    except Exception:  # noqa: BLE001
        pass


async def process_pixel_event(pool, pixel_id: str, clid: str | None,
                              event_type: str, event_name: str | None, url: str | None) -> None:
    """Match the incoming pixel event against active segments. Never raises."""
    if not clid:
        return
    try:
        device_id = await resolve_clid(clid)
        if not device_id:
            return
        segs = await pool.fetch(
            "SELECT id, rule_type, rule_value, ttl_days FROM audience_segments "
            "WHERE pixel_id = $1 AND is_active",
            pixel_id,
        )
        for seg in segs:
            if _segment_matches(seg, event_type, event_name, url):
                await add_member(pool, str(seg["id"]), seg["ttl_days"], device_id)
    except Exception:  # noqa: BLE001
        logger.warning("audience matching failed", exc_info=True)


async def check_audiences(device_id: str, include: list[str], exclude: list[str]) -> bool:
    """True if the device passes include/exclude segment rules. Fail-open."""
    if not include and not exclude:
        return True
    if not device_id:
        return not include  # unknown device can never satisfy an include list
    try:
        r = get_redis()
        pipe = r.pipeline()
        for sid in include:
            pipe.sismember(f"aud:{sid}", device_id)
        for sid in exclude:
            pipe.sismember(f"aud:{sid}", device_id)
        res = await pipe.execute()
        inc_res = res[: len(include)]
        exc_res = res[len(include):]
        if include and not any(inc_res):
            return False
        if exclude and any(exc_res):
            return False
        return True
    except Exception:  # noqa: BLE001 - fail-open: trade as before
        return True
