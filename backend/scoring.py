"""Пользовательский скоринг качества площадок по профилям.

В отличие от ml/quality.py (платформенный антифрод-скоринг, глобальный, explainable),
здесь — настраиваемые пользователем правила качества на уровне кампании/группы:
CTR, viewability, поведение из пикселя (отказы, глубина, время), CPA по цели.

Итоговый score домена = 1 - сумма штрафов за нарушенные пороги (clamp 0..1).
Домены со score < block_threshold попадают в scoring_blocklist кампании
(только если профиль enforce=true) и исключаются биддером из закупки.

Приоритет профиля в биддере: кампания > группа > нет профиля.
Fail-open: нет профиля / мало данных / ошибка — домен не блокируется.
"""
from __future__ import annotations

import asyncio
import json
import time

# Вес каждого нарушенного порога в штрафе. Одно грубое нарушение (CTR/viewability)
# опускает домен ниже дефолтного порога 0.5.
PENALTIES = {
    "low_ctr": 0.5,
    "low_viewability": 0.5,
    "high_bounce": 0.35,
    "low_depth": 0.3,
    "low_time": 0.3,
    "high_cpa": 0.6,
}

REASON_LABELS = {
    "low_ctr": "CTR ниже порога",
    "low_viewability": "Viewability ниже порога",
    "high_bounce": "Высокий показатель отказов",
    "low_depth": "Малая глубина просмотра",
    "low_time": "Мало времени на сайте",
    "high_cpa": "CPA выше цели",
}

CACHE_TTL_SEC = 300.0
_blocked_cache: dict[str, frozenset[str]] = {}
_blocked_at: float = 0.0
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def reset_cache_for_tests() -> None:
    global _blocked_cache, _blocked_at
    _blocked_cache = {}
    _blocked_at = 0.0


def score_domain(profile: dict, m: dict) -> tuple[float, list[str]]:
    """Чистая функция: оценка одного домена по профилю.

    profile — пороги (min_ctr, min_viewability, max_bounce_rate, min_avg_depth,
    min_time_on_site, max_cpa; None = правило выключено).
    m — метрики домена: ctr, viewability, bounce_rate, avg_depth, avg_time,
    cpa (None = нет конверсий/данных, правило CPA пропускается).
    Возвращает (score 0..1, reasons).
    """
    reasons: list[str] = []
    if profile.get("min_ctr") is not None and m.get("ctr") is not None and m["ctr"] < float(profile["min_ctr"]):
        reasons.append("low_ctr")
    if (profile.get("min_viewability") is not None and m.get("viewability") is not None
            and m["viewability"] < float(profile["min_viewability"])):
        reasons.append("low_viewability")
    if (profile.get("max_bounce_rate") is not None and m.get("bounce_rate") is not None
            and m["bounce_rate"] > float(profile["max_bounce_rate"])):
        reasons.append("high_bounce")
    if (profile.get("min_avg_depth") is not None and m.get("avg_depth") is not None
            and m["avg_depth"] < float(profile["min_avg_depth"])):
        reasons.append("low_depth")
    if (profile.get("min_time_on_site") is not None and m.get("avg_time") is not None
            and m["avg_time"] < float(profile["min_time_on_site"])):
        reasons.append("low_time")
    if (profile.get("max_cpa") is not None and m.get("cpa") is not None
            and m["cpa"] > float(profile["max_cpa"])):
        reasons.append("high_cpa")
    score = 1.0 - sum(PENALTIES[r] for r in reasons)
    return (max(0.0, min(1.0, score)), reasons)


async def domain_metrics(pool, user_id: str, profile: dict, campaign_id: str | None = None) -> list[dict]:
    """Метрики по доменам за окно профиля: RTB-статистика + поведение из пикселя.

    Считается только по трафику пользователя (per-user scoping), опционально —
    по конкретной кампании. Возвращает список dict с метриками и рассчитанным score.
    """
    window_days = int(profile.get("window_days") or 7)
    params: list = [user_id, window_days]
    camp_filter = ""
    if campaign_id:
        params.append(campaign_id)
        camp_filter = f" AND e.campaign_id = ${len(params)}"

    # RTB-часть: показы/клики/viewable по доменам из bid_events (scope через кампании юзера).
    rtb_rows = await pool.fetch(
        f"""
        SELECT lower(e.domain) AS domain,
               count(*) FILTER (WHERE e.event_type = 'impression') AS impressions,
               count(*) FILTER (WHERE e.event_type = 'click')      AS clicks,
               count(*) FILTER (WHERE e.event_type = 'viewable')   AS viewable_imps,
               COALESCE(sum(e.clear_price) FILTER (WHERE e.event_type = 'impression'), 0) / 1000.0 AS spend
        FROM bid_events e
        JOIN campaigns c ON c.id = e.campaign_id
        WHERE c.user_id = $1 AND e.domain IS NOT NULL AND NOT e.is_test
          AND e.created_at >= now() - make_interval(days => $2) {camp_filter}
        GROUP BY 1
        """,
        *params,
    )

    # Пиксель-часть: поведение по доменам-источникам (referrer домен = площадка).
    # Метрики агрегируются по сессиям: глубина, время, скролл и показатель отказов
    # (сессия с одним pageview, без событий и суммарным временем < 10 сек).
    behavior: dict[str, dict] = {}
    if profile.get("pixel_id"):
        b_rows = await pool.fetch(
            """
            SELECT src AS domain,
                   count(*) AS sessions,
                   COALESCE(SUM(pv), 0) AS pageviews,
                   COALESCE(SUM(dur), 0) AS total_time,
                   COALESCE(AVG(sc), 0) AS avg_scroll,
                   count(*) FILTER (WHERE pv = 1 AND ev = 0 AND dur < 10) AS bounces
            FROM (
              SELECT session_id, src,
                     count(*) FILTER (WHERE event_type = 'pageview') AS pv,
                     count(*) FILTER (WHERE event_type = 'event')    AS ev,
                     COALESCE(SUM(duration_sec), 0) AS dur,
                     COALESCE(MAX(scroll_pct), 0)  AS sc
              FROM (
                SELECT session_id, event_type, duration_sec, scroll_pct,
                       lower(regexp_replace(COALESCE(referrer, ''), '^https?://([^/]+).*$', '\\1')) AS src
                FROM pixel_events
                WHERE pixel_id = $1 AND created_at >= now() - make_interval(days => $2)
              ) e WHERE src <> ''
              GROUP BY session_id, src
            ) s
            GROUP BY src
            """,
            profile["pixel_id"], window_days,
        )
        for r in b_rows:
            sessions = int(r["sessions"] or 0)
            behavior[r["domain"]] = {
                "avg_depth": round(int(r["pageviews"] or 0) / sessions, 2) if sessions else None,
                "avg_time": round(float(r["total_time"] or 0) / sessions, 1) if sessions else None,
                "avg_scroll": round(float(r["avg_scroll"] or 0), 1),
                "bounce_rate": round(int(r["bounces"] or 0) / sessions * 100, 1) if sessions else None,
            }

    # Отказы и CPA по доменам из пиксель-конверсий (если задана цель).
    cpa_by_domain: dict[str, float] = {}
    if profile.get("goal_id"):
        c_rows = await pool.fetch(
            """SELECT lower(domain) AS domain, count(*) AS conv, COALESCE(SUM(value),0) AS val
               FROM conversions
               WHERE goal_id = $1 AND domain IS NOT NULL AND NOT is_test
                 AND created_at >= now() - make_interval(days => $2)
               GROUP BY 1""",
            profile["goal_id"], window_days,
        )
        conv_by_domain = {r["domain"]: int(r["conv"]) for r in c_rows}
    else:
        conv_by_domain = {}

    # Текущее состояние блок-листа: авто (по профилю) и ручные блоки оператора.
    # Без campaign_id блок-лист не привязан к одной кампании — показываем факт
    # блокировки хотя бы в одной кампании пользователя.
    blocks: dict[str, dict] = {}
    if campaign_id:
        b_rows = await pool.fetch(
            "SELECT domain, source, note, updated_at FROM scoring_blocklist WHERE campaign_id = $1",
            campaign_id,
        )
    else:
        b_rows = await pool.fetch(
            """SELECT b.domain, min(b.source) AS source, NULL::text AS note, max(b.updated_at) AS updated_at
               FROM scoring_blocklist b JOIN campaigns c ON c.id = b.campaign_id
               WHERE c.user_id = $1 GROUP BY b.domain""",
            user_id,
        )
    for r in b_rows:
        blocks[r["domain"]] = {
            "source": r["source"],
            "note": r["note"],
            "at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }

    min_imps = int(profile.get("min_impressions") or 0)
    min_clicks = int(profile.get("min_clicks") or 0)
    out = []
    for r in rtb_rows:
        dom = r["domain"]
        imps, clicks = int(r["impressions"]), int(r["clicks"])
        viewable = int(r["viewable_imps"])
        spend = float(r["spend"])
        beh = behavior.get(dom, {})
        conv = conv_by_domain.get(dom, 0)
        cpa = round(spend / conv, 2) if conv > 0 else None
        blk = blocks.get(dom)
        m = {
            "domain": dom,
            "impressions": imps,
            "clicks": clicks,
            "viewable_imps": viewable,
            "spend": round(spend, 2),
            "conversions": conv,
            "ctr": round(clicks / imps * 100, 3) if imps else 0.0,
            "viewability": round(viewable / imps * 100, 1) if imps else 0.0,
            "avg_depth": beh.get("avg_depth"),
            "avg_time": beh.get("avg_time"),
            "avg_scroll": beh.get("avg_scroll"),
            "bounce_rate": beh.get("bounce_rate"),
            "cpa": cpa,
            "blocked": blk is not None,
            "block_source": blk["source"] if blk else None,
            "block_note": blk["note"] if blk else None,
            "blocked_at": blk["at"] if blk else None,
        }
        # Активируем скоринг только при достаточных данных (иначе score=None: не блокируем).
        if imps >= min_imps and clicks >= min_clicks:
            score, reasons = score_domain(profile, m)
            m["score"] = score
            m["reasons"] = reasons
            m["flagged"] = score < float(profile.get("block_threshold") or 0.5)
        else:
            m["score"] = None
            m["reasons"] = []
            m["flagged"] = False
        out.append(m)
    out.sort(key=lambda x: (x["score"] if x["score"] is not None else 1.0, -x["impressions"]))
    return out


async def recompute_blocklist(pool, campaign_id: str, user_id: str, profile: dict) -> dict:
    """Пересчёт scoring_blocklist кампании по профилю (для enforce=true)."""
    rows = await domain_metrics(pool, user_id, profile, campaign_id=campaign_id)
    blocked = [r for r in rows if r["flagged"] and r["score"] is not None]
    # Пересчёт трогает только авто-блоки; ручные блоки оператора не сносим.
    await pool.execute(
        "DELETE FROM scoring_blocklist WHERE campaign_id = $1 AND source = 'auto'", campaign_id
    )
    for r in blocked:
        await pool.execute(
            """INSERT INTO scoring_blocklist (campaign_id, domain, score, reasons, source, updated_at)
               VALUES ($1, $2, $3, $4, 'auto', now())
               ON CONFLICT (campaign_id, domain) DO UPDATE
                 SET score = EXCLUDED.score, reasons = EXCLUDED.reasons, updated_at = now()
               WHERE scoring_blocklist.source = 'auto'""",
            campaign_id, r["domain"], r["score"], json.dumps(r["reasons"]),
        )
    reset_cache_for_tests()
    return {"scored": len(rows), "blocked": len(blocked)}


async def get_scoring_blocks(pool, campaign_id: str) -> frozenset[str]:
    """Set доменов из scoring_blocklist кампании. Кэш TTL 5 мин, fail-open."""
    global _blocked_cache, _blocked_at
    now = time.monotonic()
    if now - _blocked_at >= CACHE_TTL_SEC:
        lock = _get_lock()
        if not lock.locked():
            async with lock:
                try:
                    rows = await pool.fetch("SELECT campaign_id, domain FROM scoring_blocklist")
                    cache: dict[str, set[str]] = {}
                    for r in rows:
                        cache.setdefault(str(r["campaign_id"]), set()).add(r["domain"])
                    _blocked_cache = {k: frozenset(v) for k, v in cache.items()}
                except Exception:
                    pass
                _blocked_at = time.monotonic()
    return _blocked_cache.get(str(campaign_id), frozenset())
