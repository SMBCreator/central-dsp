"""Сходимость bid_events -> stats_hourly (-> /stats/summary).

    python scripts/convergence_check.py                       # последние 24 ч
    python scripts/convergence_check.py --hours 72
    python scripts/convergence_check.py --since 2026-09-01T00:00Z --until 2026-09-02T00:00Z
    python scripts/convergence_check.py --api-url https://host --cookie "session=..."

Что сравнивает (по часам, в UTC, окно [since, until)):
  bid_events  count(bid) / count(win) / count(impression) / count(click) /
              sum(clear_price по win)/1000   (stats_math.spend_expr — та же формула,
              что /stats/report и карточка SSP; деньги списываются на win — E-01)
  stats_hourly sum(bids) / sum(wins) / sum(impressions) / sum(clicks) / sum(spend)
  --api-url    GET /stats/summary?hours=N платформенной сессией (superadmin/admin/
              analyst/finance) против stats_hourly с тем же предикатом
              ts_hour >= now() - N часов. Окно API привязано к now(), поэтому
              --api-url несовместим с --since/--until.

Тестовые события (is_test) исключены с обеих сторон — stats_hourly их не
агрегирует. Критерий: расхождение по счётчикам 0 строк (--tolerance-rows),
по расходу <= 0.01 (--tolerance-spend). Выход 1 при провале, 2 при ошибке
подключения. Скрипт только читает.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stats_math import spend_expr  # noqa: E402

_EVENTS_SQL = f"""
SELECT date_trunc('hour', e.created_at) AS ts_hour,
       count(*) FILTER (WHERE e.event_type = 'bid')        AS bids,
       count(*) FILTER (WHERE e.event_type = 'win')        AS wins,
       count(*) FILTER (WHERE e.event_type = 'impression') AS impressions,
       count(*) FILTER (WHERE e.event_type = 'click')      AS clicks,
       {spend_expr("e")} AS spend
FROM bid_events e
WHERE e.created_at >= $1 AND e.created_at < $2 AND NOT e.is_test
GROUP BY 1
"""

_HOURLY_SQL = """
SELECT ts_hour,
       sum(bids) AS bids, sum(wins) AS wins, sum(impressions) AS impressions,
       sum(clicks) AS clicks, sum(spend) AS spend
FROM stats_hourly
WHERE ts_hour >= $1 AND ts_hour < $2
GROUP BY 1
"""

METRICS = ("bids", "wins", "impressions", "clicks", "spend")


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row_map(rows) -> dict[datetime, dict[str, float]]:
    return {r["ts_hour"]: {m: float(r[m] or 0) for m in METRICS} for r in rows}


def compare(events: dict, hourly: dict, tol_rows: int, tol_spend: float) -> list[dict]:
    """Список расхождений по часам; пустой список = сходится."""
    diffs = []
    for ts in sorted(set(events) | set(hourly)):
        e = events.get(ts, dict.fromkeys(METRICS, 0.0))
        h = hourly.get(ts, dict.fromkeys(METRICS, 0.0))
        bad = {}
        for m in METRICS:
            delta = h[m] - e[m]
            tol = tol_spend if m == "spend" else tol_rows
            if abs(delta) > tol:
                bad[m] = {"bid_events": e[m], "stats_hourly": h[m], "delta": round(delta, 4)}
        if bad:
            diffs.append({"ts_hour": ts.isoformat(), **bad})
    return diffs


def totals(rows: dict) -> dict[str, float]:
    out = dict.fromkeys(METRICS, 0.0)
    for r in rows.values():
        for m in METRICS:
            out[m] += r[m]
    out["spend"] = round(out["spend"], 2)
    return out


PLATFORM_SCOPE_ROLES = ("superadmin", "admin", "analyst", "finance")

# Тот же предикат, что в routers/stats.py summary(hours=...): без верхней границы,
# от now() в момент запроса — чтобы сравнивать одинаковые часовые корзины.
_HOURLY_API_WINDOW_SQL = """
SELECT sum(bids) AS bids, sum(wins) AS wins, sum(impressions) AS impressions,
       sum(clicks) AS clicks, sum(spend) AS spend
FROM stats_hourly
WHERE ts_hour >= now() - ($1::int * interval '1 hour')
"""


async def fetch_api_summary(api_url: str, cookie: str, hours: int) -> tuple[dict | None, str | None]:
    """(summary, None) или (None, причина). Проверяет, что сессия платформенная:
    у остальных ролей scope ограничен владельцем и суммы несопоставимы."""
    import httpx

    base = api_url.rstrip("/")
    headers = {"Cookie": cookie}
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            me = await c.get(f"{base}/auth/me", headers=headers)
            if me.status_code != 200:
                return None, f"/auth/me -> HTTP {me.status_code}: cookie невалидна или истекла"
            role = me.json().get("role")
            if role not in PLATFORM_SCOPE_ROLES:
                return None, f"роль сессии {role!r} не платформенная ({', '.join(PLATFORM_SCOPE_ROLES)}) — суммы несопоставимы"
            r = await c.get(f"{base}/stats/summary", params={"hours": hours}, headers=headers)
            if r.status_code != 200:
                return None, f"/stats/summary -> HTTP {r.status_code}: {r.text[:200]}"
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        return None, f"API недоступен: {type(exc).__name__}: {exc}"
    if not isinstance(data, dict) or "spend" not in data:
        return None, f"/stats/summary вернул не summary: {str(data)[:200]}"
    return data, None


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--tolerance-rows", type=int, default=0)
    p.add_argument("--tolerance-spend", type=float, default=0.01)
    p.add_argument("--api-url", help="если задан — сверить ещё и /stats/summary")
    p.add_argument("--cookie", help='cookie сессии для --api-url, напр. "session=..."')
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    until = _parse_ts(args.until) if args.until else datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    since = _parse_ts(args.since) if args.since else until - timedelta(hours=args.hours)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL не задан", file=sys.stderr)
        return 2
    if args.api_url and (args.since or args.until):
        print("--api-url несовместим с --since/--until: окно /stats/summary всегда от now()", file=sys.stderr)
        return 2
    try:
        conn = await asyncpg.connect(dsn)
    except Exception as exc:  # noqa: BLE001
        print(f"нет соединения с Postgres: {exc}", file=sys.stderr)
        return 2
    try:
        events = _row_map(await conn.fetch(_EVENTS_SQL, since, until))
        hourly = _row_map(await conn.fetch(_HOURLY_SQL, since, until))
        hourly_api_window = None
        if args.api_url:
            row = await conn.fetchrow(_HOURLY_API_WINDOW_SQL, args.hours)
            hourly_api_window = {m: float(row[m] or 0) for m in METRICS}
    finally:
        await conn.close()

    diffs = compare(events, hourly, args.tolerance_rows, args.tolerance_spend)
    report = {
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "bid_events": totals(events),
        "stats_hourly": totals(hourly),
        "hours_with_diff": len(diffs),
        "diffs": diffs[:48],
        "pass": not diffs,
    }

    if args.api_url:
        if not args.cookie:
            print("--api-url требует --cookie", file=sys.stderr)
            return 2
        summary, why = await fetch_api_summary(args.api_url, args.cookie, args.hours)
        if summary is None:
            print(f"сверка с API не выполнена: {why}", file=sys.stderr)
            return 2
        api = {m: float(summary.get(m, 0) or 0) for m in METRICS}
        api_bad = {
            m: {"stats_hourly": hourly_api_window[m], "api": api[m]}
            for m in METRICS
            if abs(api[m] - hourly_api_window[m]) > (args.tolerance_spend if m == "spend" else args.tolerance_rows)
        }
        report["api_window"] = {"hours": args.hours, "stats_hourly": hourly_api_window, "api": api}
        report["api_diff"] = api_bad
        report["pass"] = report["pass"] and not api_bad

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        w = report["window"]
        print(f"окно {w['since']} .. {w['until']} (UTC)")
        print(f"{'':14}{'bid_events':>14}{'stats_hourly':>14}")
        for m in METRICS:
            print(f"{m:14}{report['bid_events'][m]:>14,.2f}{report['stats_hourly'][m]:>14,.2f}")
        if "api_window" in report:
            aw = report["api_window"]
            print(f"api /stats/summary?hours={aw['hours']}:", aw["api"], "stats_hourly:", aw["stats_hourly"],
                  "diff:", report["api_diff"] or "нет")
        if diffs:
            print(f"\nРАСХОЖДЕНИЯ в {len(diffs)} час(ах):")
            for d in diffs[:20]:
                print(" ", d)
        print("\nPASS" if report["pass"] else "\nFAIL")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
