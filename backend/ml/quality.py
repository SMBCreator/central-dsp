"""Качество доменов: explainable-скоринг для clean-traffic режима.

Пересчёт (recompute, крон /cron/domain-quality):
  * окно 7 дней bid_events по доменам: wins / impressions / clicks;
  * правила со штрафами (каждое объяснимо и записано в reasons):
      ctr_anomaly   — CTR > 8x глобального при >= 500 показов (клик-ферма);
      click_burst   — кликов больше, чем показов (невозможная последовательность);
      imp_no_win    — показов в 3+ раза больше, чем win-ов (replay/дубли пикселя);
      zero_engage   — >= 2000 показов и 0 кликов (мёртвый/фродовый инвентарь);
  * score = 1 - сумма штрафов (clamp 0..1). История не переписывается —
    таблица domain_quality хранит последний snapshot + reasons.

Hot path (clean-traffic): get_blocked_domains() — set доменов ниже порога
из in-process кэша (TTL 5 мин, fail-open: нет данных => пустой set).
"""
from __future__ import annotations

import asyncio
import json
import time

WINDOW_DAYS = 7
MIN_IMPS_FOR_SCORING = 100          # меньше — не скорим (недостаточно данных)
CTR_ANOMALY_MULT = 8.0
CTR_ANOMALY_MIN_IMPS = 500
IMP_WIN_RATIO = 3.0
ZERO_ENGAGE_IMPS = 2000

PENALTIES = {
    # Аномалии, достоверно указывающие на фрод (ctr_anomaly, click_burst),
    # ДОЛЖНЫ в одиночку опускать score ниже CLEAN_SCORE_THRESHOLD (0.5).
    "ctr_anomaly": 0.6,
    "click_burst": 0.8,
    "imp_no_win": 0.4,
    "zero_engage": 0.3,
}

# Порог clean-traffic: домены со score ниже — исключаются из закупки
CLEAN_SCORE_THRESHOLD = 0.5
CACHE_TTL_SEC = 300.0

_blocked_cache: frozenset[str] | None = None
_blocked_at: float = 0.0
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def reset_cache_for_tests() -> None:
    global _blocked_cache, _blocked_at
    _blocked_cache = None
    _blocked_at = 0.0


def set_blocked_for_tests(domains: set[str]) -> None:
    global _blocked_cache, _blocked_at
    _blocked_cache = frozenset(d.lower() for d in domains)
    _blocked_at = time.monotonic()


def score_domain(*, imps: int, clicks: int, wins: int, global_ctr: float) -> tuple[float, list[str]]:
    """Чистая функция скоринга одного домена. Возвращает (score, reasons)."""
    reasons: list[str] = []
    if clicks > imps:
        reasons.append("click_burst")
    if imps >= CTR_ANOMALY_MIN_IMPS and global_ctr > 0 and clicks / max(imps, 1) > CTR_ANOMALY_MULT * global_ctr:
        reasons.append("ctr_anomaly")
    if wins > 0 and imps > IMP_WIN_RATIO * wins:
        reasons.append("imp_no_win")
    if imps >= ZERO_ENGAGE_IMPS and clicks == 0:
        reasons.append("zero_engage")
    score = 1.0 - sum(PENALTIES[r] for r in reasons)
    return (max(0.0, min(1.0, score)), reasons)


async def recompute(pool) -> dict:
    """Пересчёт domain_quality по окну. Вызывается кроном."""
    window = await pool.fetchrow(f"SELECT now() - interval '{WINDOW_DAYS} days' AS w_from")
    totals = await pool.fetchrow(
        "SELECT count(*) FILTER (WHERE event_type = 'impression') AS imps, "
        "       count(*) FILTER (WHERE event_type = 'click') AS clicks "
        "FROM bid_events WHERE created_at >= $1",
        window["w_from"],
    )
    g_imps = int(totals["imps"] or 0)
    g_clicks = int(totals["clicks"] or 0)
    global_ctr = (g_clicks / g_imps) if g_imps > 0 else 0.0

    rows = await pool.fetch(
        "SELECT lower(domain) AS domain, "
        "       count(*) FILTER (WHERE event_type = 'impression') AS imps, "
        "       count(*) FILTER (WHERE event_type = 'click') AS clicks, "
        "       count(*) FILTER (WHERE event_type = 'win') AS wins "
        "FROM bid_events "
        "WHERE created_at >= $1 AND domain IS NOT NULL "
        "GROUP BY 1 HAVING count(*) FILTER (WHERE event_type = 'impression') >= $2",
        window["w_from"], MIN_IMPS_FOR_SCORING,
    )
    model_row = await pool.fetchrow(
        "SELECT version FROM ml_models WHERE model_type = 'pctr' AND status = 'active' "
        "ORDER BY version DESC LIMIT 1"
    )
    model_version = int(model_row["version"]) if model_row else None

    scored = flagged = 0
    for r in rows:
        score, reasons = score_domain(
            imps=int(r["imps"]), clicks=int(r["clicks"]), wins=int(r["wins"]),
            global_ctr=global_ctr,
        )
        await pool.execute(
            "INSERT INTO domain_quality (domain, score, imps, clicks, wins, reasons, "
            "                            model_version, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, now()) "
            "ON CONFLICT (domain) DO UPDATE SET "
            "  score = EXCLUDED.score, imps = EXCLUDED.imps, clicks = EXCLUDED.clicks, "
            "  wins = EXCLUDED.wins, reasons = EXCLUDED.reasons, "
            "  model_version = EXCLUDED.model_version, updated_at = now()",
            r["domain"], score, int(r["imps"]), int(r["clicks"]), int(r["wins"]),
            json.dumps(reasons), model_version,
        )
        scored += 1
        if score < CLEAN_SCORE_THRESHOLD:
            flagged += 1
    reset_cache_for_tests()
    return {"scored": scored, "flagged": flagged, "global_ctr": round(global_ctr, 6),
            "threshold": CLEAN_SCORE_THRESHOLD}


async def get_blocked_domains(pool) -> frozenset[str]:
    """Set доменов ниже порога для clean-traffic кампаний. Fail-open: пустой set."""
    global _blocked_cache, _blocked_at
    now = time.monotonic()
    if _blocked_cache is not None and now - _blocked_at < CACHE_TTL_SEC:
        return _blocked_cache
    lock = _get_lock()
    if lock.locked():
        return _blocked_cache if _blocked_cache is not None else frozenset()
    async with lock:
        now = time.monotonic()
        if _blocked_cache is not None and now - _blocked_at < CACHE_TTL_SEC:
            return _blocked_cache
        try:
            rows = await pool.fetch(
                "SELECT domain FROM domain_quality WHERE score < $1", CLEAN_SCORE_THRESHOLD
            )
            _blocked_cache = frozenset(r["domain"] for r in rows)
        except Exception:
            _blocked_cache = _blocked_cache if _blocked_cache is not None else frozenset()
        _blocked_at = now
        return _blocked_cache
