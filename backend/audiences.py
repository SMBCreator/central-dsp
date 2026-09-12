"""Retargeting audiences: pixel-driven segments with Redis-backed membership.

Data flow:
  1. Bid hot path stores a short-lived bridge  clidmap:{bid_id} -> device_id
     (fire-and-forget; the click redirect appends cdsp_clid={bid_id} to the
     landing URL, so the pixel reports events with clid = bid_id).
  2. Pixel /collect resolves clid -> device_id and matches active segments of
     that pixel; membership goes to Postgres (truth) + Redis ZSET (hot path).
     Goal segments (rule_type='goal') are filled from the goal-conversion path
     (pixel._match_goals -> add_goal_members).
  3. The bidder checks targeting audience_include / audience_exclude with
     ZSCORE > now — fail-open on any Redis error.

Redis keys:
  clidmap:{bid_id}      device_id                             TTL 7d
  audz:{segment_id}     ZSET device_id -> expires_at (unix)   key TTL = max ttl + grace

Audit R-06: the previous aud:{segment_id} SET had one EXPIRE for the whole
key, so every new member prolonged every old one — a "30 day" segment never
forgot anybody while traffic kept coming. Per-member expiry (ZSET score)
mirrors audience_members.expires_at exactly. The old aud:* SETs are simply
ignored and die by their TTL; scripts/redis_rebuild_audiences.py seeds audz:*
from Postgres after the deploy (and after any Redis loss).
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from cache import get_redis

logger = logging.getLogger("audiences")

CLIDMAP_TTL = 7 * 24 * 3600
AUDIENCE_PREFIX = "audz:"
MAX_TTL_DAYS = 180                 # audience_segments.ttl_days CHECK
_KEY_GRACE = 24 * 3600             # key TTL = member ttl + grace (GC only)


def audience_key(segment_id: str) -> str:
    return f"{AUDIENCE_PREFIX}{segment_id}"


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
    # goal segments are filled by the goal-conversion path (add_goal_members)
    return False


async def redis_add_member(segment_id: str, device_id: str, expires_at: datetime, ttl_days: int) -> None:
    """Mirror one membership into the audz ZSET. Best-effort, never raises.

    ZADD with score = expires_at; overdue members of the same segment are
    pruned on the way (cheap: the index is ordered by score). The key TTL is
    only a GC safety net for segments that stop receiving traffic.
    """
    try:
        r = get_redis()
        key = audience_key(segment_id)
        now = int(time.time())
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, "-inf", now)
        pipe.zadd(key, {device_id: int(expires_at.timestamp())})
        pipe.expire(key, min(int(ttl_days), MAX_TTL_DAYS) * 24 * 3600 + _KEY_GRACE)
        await pipe.execute()
    except Exception:  # noqa: BLE001
        pass


async def add_member(pool, segment_id: str, ttl_days: int, device_id: str) -> None:
    """Persist membership (Postgres = truth) + mirror to Redis (best-effort)."""
    expires = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    await pool.execute(
        """INSERT INTO audience_members (segment_id, device_id, expires_at)
           VALUES ($1, $2, $3)
           ON CONFLICT (segment_id, device_id) DO UPDATE SET expires_at = EXCLUDED.expires_at""",
        segment_id, device_id, expires,
    )
    await redis_add_member(segment_id, device_id, expires, ttl_days)


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
            "WHERE pixel_id = $1 AND is_active AND rule_type <> 'goal'",
            pixel_id,
        )
        for seg in segs:
            if _segment_matches(seg, event_type, event_name, url):
                await add_member(pool, str(seg["id"]), seg["ttl_days"], device_id)
    except Exception:  # noqa: BLE001
        logger.warning("audience matching failed", exc_info=True)


async def add_goal_members(pool, pixel_id: str, goal_id: str, clid: str | None) -> None:
    """A goal converted: fill every active segment with rule_type='goal' whose
    rule_value is this goal (R-06 — the rule existed in the schema and UI but
    nothing populated it). Never raises."""
    if not clid:
        return
    try:
        device_id = await resolve_clid(clid)
        if not device_id:
            return
        segs = await pool.fetch(
            "SELECT id, ttl_days FROM audience_segments "
            "WHERE pixel_id = $1 AND is_active AND rule_type = 'goal' AND rule_value = $2",
            pixel_id, str(goal_id),
        )
        for seg in segs:
            await add_member(pool, str(seg["id"]), seg["ttl_days"], device_id)
    except Exception:  # noqa: BLE001
        logger.warning("goal audience fill failed", exc_info=True)


async def check_audiences(device_id: str, include: list[str], exclude: list[str]) -> bool:
    """True if the device passes include/exclude segment rules. Fail-open.

    Membership = ZSCORE exists and is still in the future; an expired member
    that the pruner has not removed yet is already not a member."""
    if not include and not exclude:
        return True
    if not device_id:
        return not include  # unknown device can never satisfy an include list
    try:
        r = get_redis()
        pipe = r.pipeline()
        for sid in include:
            pipe.zscore(audience_key(sid), device_id)
        for sid in exclude:
            pipe.zscore(audience_key(sid), device_id)
        res = await pipe.execute()
        now = time.time()
        member = [s is not None and float(s) > now for s in res]
        inc_res = member[: len(include)]
        exc_res = member[len(include):]
        if include and not any(inc_res):
            return False
        if exclude and any(exc_res):
            return False
        return True
    except Exception:  # noqa: BLE001 - fail-open: trade as before
        return True


# --- Postgres <-> Redis sync (cron /cron/audience-sync, scripts/redis_rebuild_audiences.py)

async def sync_audiences(pool, *, reseed_empty: bool = True, batch: int = 5000) -> dict:
    """Keep the Redis mirror honest against audience_members (R-06).

    1. Postgres: delete expired rows (they are the retention boundary).
    2. Redis: ZREMRANGEBYSCORE overdue members of every active segment.
    3. Self-heal: a segment with live members in Postgres but an empty/missing
       audz: key (Redis lost, deploy from aud: SETs) is re-seeded in batches.
    Never raises on Redis errors; Postgres errors propagate (cron marks a
    failed run)."""
    now = int(time.time())
    deleted = await pool.fetchval(
        "WITH d AS (DELETE FROM audience_members WHERE expires_at <= now() RETURNING 1) SELECT count(*) FROM d"
    )
    segs = await pool.fetch(
        "SELECT s.id, s.ttl_days, COUNT(m.device_id) AS live "
        "FROM audience_segments s LEFT JOIN audience_members m "
        "  ON m.segment_id = s.id AND m.expires_at > now() "
        "WHERE s.is_active GROUP BY s.id, s.ttl_days"
    )
    pruned = reseeded_segments = reseeded_members = 0
    try:
        r = get_redis()
    except Exception:  # noqa: BLE001
        return {"pg_deleted": int(deleted or 0), "segments": len(segs), "redis": "unavailable"}
    for seg in segs:
        sid = str(seg["id"])
        key = audience_key(sid)
        try:
            pruned += int(await r.zremrangebyscore(key, "-inf", now) or 0)
            if not reseed_empty or int(seg["live"] or 0) == 0:
                continue
            if int(await r.zcard(key) or 0) > 0:
                continue
        except Exception:  # noqa: BLE001
            continue
        # Re-seed from Postgres in batches.
        offset = 0
        while True:
            rows = await pool.fetch(
                "SELECT device_id, expires_at FROM audience_members "
                "WHERE segment_id = $1 AND expires_at > now() ORDER BY device_id LIMIT $2 OFFSET $3",
                seg["id"], batch, offset,
            )
            if not rows:
                break
            try:
                await r.zadd(key, {row["device_id"]: int(row["expires_at"].timestamp()) for row in rows})
            except Exception:  # noqa: BLE001
                break
            reseeded_members += len(rows)
            offset += len(rows)
            if len(rows) < batch:
                break
        try:
            await r.expire(key, min(int(seg["ttl_days"]), MAX_TTL_DAYS) * 24 * 3600 + _KEY_GRACE)
        except Exception:  # noqa: BLE001
            pass
        reseeded_segments += 1
    return {
        "pg_deleted": int(deleted or 0), "segments": len(segs), "redis_pruned": pruned,
        "reseeded_segments": reseeded_segments, "reseeded_members": reseeded_members,
    }
