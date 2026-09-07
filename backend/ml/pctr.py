"""pCTR: сегментная Beta-сглаженная модель с лог-odds комбинацией.

Обучение (train, вызывается кроном):
  * окно 14 дней bid_events: impressions и clicks по сегментам
    domain / creative / device_type / hour;
  * Beta-смузинг: ctr_seg = (clicks + a) / (imps + a + b),
    a = global_ctr * S, b = (1 - global_ctr) * S, S = сила приора;
  * новая версия в ml_models (+ сегменты в ml_pctr_segments), прежняя retired.

Скоринг (predict, hot path):
  * сегменты активной версии кэшируются в памяти процесса (TTL 5 мин,
    single-flight, fail-open: нет кэша/модели -> None);
  * лог-odds: logit(pctr) = logit(g) + sum(logit(ctr_seg) - logit(g)),
    т.е. каждый сегмент сдвигает глобальный приор на свою поправку.

Не выполняет сетевых вызовов в predict(). Никаких исключений наружу.
"""
from __future__ import annotations

import asyncio
import math
import time

# ------------------------------------------------------------------ config --

TRAIN_WINDOW_DAYS = 14
PRIOR_STRENGTH = 200.0        # S: чем больше, тем сильнее тянет к глобальному CTR
MIN_SEGMENT_IMPS = 50         # сегменты тоньше — не сохраняем (шум)
CACHE_TTL_SEC = 300.0

SEGMENT_TYPES = ("domain", "creative", "device_type", "hour")

# ------------------------------------------------------------------- cache --

_cache: dict | None = None    # {"version": int, "global_ctr": float, "segments": {(type,key): ctr}}
_cache_at: float = 0.0
_cache_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def reset_cache_for_tests() -> None:
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


def set_cache_for_tests(version: int, global_ctr: float, segments: dict) -> None:
    """Тестовый шов: подложить модель без БД."""
    global _cache, _cache_at
    _cache = {"version": version, "global_ctr": global_ctr, "segments": segments}
    _cache_at = time.monotonic()


async def _load_active_model(pool) -> dict | None:
    row = await pool.fetchrow(
        "SELECT version, metrics FROM ml_models "
        "WHERE model_type = 'pctr' AND status = 'active' "
        "ORDER BY version DESC LIMIT 1"
    )
    if not row:
        return None
    import json
    metrics = row["metrics"]
    if isinstance(metrics, str):
        metrics = json.loads(metrics or "{}")
    global_ctr = float((metrics or {}).get("global_ctr") or 0)
    if global_ctr <= 0 or global_ctr >= 1:
        return None
    segs = await pool.fetch(
        "SELECT segment_type, segment_key, ctr_smoothed FROM ml_pctr_segments "
        "WHERE model_version = $1",
        row["version"],
    )
    segments = {(s["segment_type"], s["segment_key"]): float(s["ctr_smoothed"]) for s in segs}
    return {"version": int(row["version"]), "global_ctr": global_ctr, "segments": segments}


async def _ensure_cache(pool) -> dict | None:
    """TTL-кэш активной модели; single-flight; fail-open."""
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and now - _cache_at < CACHE_TTL_SEC:
        return _cache
    lock = _get_lock()
    if lock.locked():           # кто-то уже грузит — работаем со старым кэшем
        return _cache
    async with lock:
        now = time.monotonic()
        if _cache is not None and now - _cache_at < CACHE_TTL_SEC:
            return _cache
        try:
            model = await _load_active_model(pool)
        except Exception:
            _cache_at = now      # не долбим БД при ошибке — ждём следующего TTL
            return _cache
        _cache = model
        _cache_at = now
        return _cache


# ----------------------------------------------------------------- predict --

def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1 / (1 + z)
    z = math.exp(x)
    return z / (1 + z)


async def predict(pool, *, domain: str | None, creative_id: str | None,
                  device_type: str | None, hour: int | None) -> dict | None:
    """pCTR для комбинации фич. None => модель недоступна (базовый биддинг).

    Возвращает {"pctr", "baseline", "version", "matched"}:
    baseline — глобальный CTR модели (для value-множителя pctr/baseline),
    matched — сколько сегментов нашлось (0 = чистый приор).
    """
    try:
        model = await _ensure_cache(pool)
    except Exception:
        return None
    if model is None:
        return None
    g = model["global_ctr"]
    segments = model["segments"]
    x = _logit(g)
    matched = 0
    for seg_type, key in (
        ("domain", (domain or "").lower() or None),
        ("creative", creative_id),
        ("device_type", device_type),
        ("hour", str(hour) if hour is not None else None),
    ):
        if key is None:
            continue
        ctr = segments.get((seg_type, key))
        if ctr is not None:
            x += _logit(ctr) - _logit(g)
            matched += 1
    pctr = g if matched == 0 else _sigmoid(x)
    return {"pctr": pctr, "baseline": g, "version": model["version"], "matched": matched}


# --------------------------------------------------------------------- cpc --

# Ожидаемый CTR для CPC-ставок, когда pCTR-модели нет вовсе (bootstrap):
# 0.2% — консервативный display-CTR. Зеркало bidder-go pctr.CPCFallbackCTR.
CPC_FALLBACK_CTR = 0.002


async def expected_ctr(pool, *, domain: str | None, creative_id: str | None,
                       device_type: str | None, hour: int | None) -> float:
    """CTR для конвертации CPC -> CPM: прогноз модели (при matched=0 — её
    глобальный CTR), без модели — CPC_FALLBACK_CTR. Никогда не 0."""
    pred = await predict(pool, domain=domain, creative_id=creative_id,
                         device_type=device_type, hour=hour)
    if pred is None or pred["pctr"] <= 0:
        return CPC_FALLBACK_CTR
    return pred["pctr"]


def cpc_to_cpm(bid_cpc: float, ctr: float) -> float:
    """Ставка за клик -> эквивалентная CPM-ставка: bid_cpc × CTR × 1000."""
    return bid_cpc * ctr * 1000.0


# ------------------------------------------------------------------- train --

async def train(pool) -> dict:
    """Пересчёт модели по окну bid_events. Вызывается кроном /cron/ml-train.

    Возвращает сводку для ответа крона и аудита.
    """
    window = await pool.fetchrow(
        f"SELECT now() - interval '{TRAIN_WINDOW_DAYS} days' AS w_from, now() AS w_to"
    )
    totals = await pool.fetchrow(
        "SELECT count(*) FILTER (WHERE event_type = 'impression') AS imps, "
        "       count(*) FILTER (WHERE event_type = 'click') AS clicks "
        "FROM bid_events WHERE created_at >= $1",
        window["w_from"],
    )
    imps = int(totals["imps"] or 0)
    clicks = int(totals["clicks"] or 0)
    if imps < 1000:
        return {"trained": False, "reason": "insufficient_data", "imps": imps,
                "min_required": 1000}
    global_ctr = clicks / imps
    if global_ctr <= 0:
        return {"trained": False, "reason": "zero_ctr", "imps": imps}

    a = global_ctr * PRIOR_STRENGTH
    b = (1 - global_ctr) * PRIOR_STRENGTH

    seg_rows: list[tuple[str, str, int, int, float]] = []
    seg_queries = {
        "domain": "lower(domain)",
        "creative": "creative_id::text",
        "device_type": "device_type",
        "hour": "extract(hour FROM created_at)::int::text",
    }
    for seg_type, expr in seg_queries.items():
        rows = await pool.fetch(
            f"SELECT {expr} AS key, "
            "       count(*) FILTER (WHERE event_type = 'impression') AS imps, "
            "       count(*) FILTER (WHERE event_type = 'click') AS clicks "
            "FROM bid_events "
            "WHERE created_at >= $1 AND event_type IN ('impression','click') "
            f"  AND {expr} IS NOT NULL "
            f"GROUP BY 1 HAVING count(*) FILTER (WHERE event_type = 'impression') >= $2",
            window["w_from"], MIN_SEGMENT_IMPS,
        )
        for r in rows:
            s_imps = int(r["imps"])
            s_clicks = int(r["clicks"])
            ctr = (s_clicks + a) / (s_imps + a + b)
            seg_rows.append((seg_type, str(r["key"]), s_imps, s_clicks, ctr))

    import json
    async with pool.acquire() as conn:
        async with conn.transaction():
            ver_row = await conn.fetchrow(
                "SELECT coalesce(max(version), 0) + 1 AS v FROM ml_models WHERE model_type = 'pctr'"
            )
            version = int(ver_row["v"])
            await conn.execute(
                "UPDATE ml_models SET status = 'retired' WHERE model_type = 'pctr' AND status = 'active'"
            )
            await conn.execute(
                "INSERT INTO ml_models (model_type, version, status, window_from, window_to, "
                "                       sample_events, metrics, params) "
                "VALUES ('pctr', $1, 'active', $2, $3, $4, $5, $6)",
                version, window["w_from"], window["w_to"], imps + clicks,
                json.dumps({"global_ctr": global_ctr, "segments": len(seg_rows),
                            "imps": imps, "clicks": clicks}),
                json.dumps({"prior_strength": PRIOR_STRENGTH,
                            "min_segment_imps": MIN_SEGMENT_IMPS,
                            "formula": "beta_logodds_v1"}),
            )
            for seg_type, key, s_imps, s_clicks, ctr in seg_rows:
                await conn.execute(
                    "INSERT INTO ml_pctr_segments (model_version, segment_type, segment_key, "
                    "                              imps, clicks, ctr_smoothed) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    version, seg_type, key, s_imps, s_clicks, ctr,
                )
    reset_cache_for_tests()  # инвалидация кэша процесса: подтянет новую версию
    return {"trained": True, "version": version, "global_ctr": round(global_ctr, 6),
            "segments": len(seg_rows), "imps": imps, "clicks": clicks}
