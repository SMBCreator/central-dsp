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
  --api-url    GET /stats/summary?from=&to= для того же окна (расход/показы/клики).

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


async def fetch_api_summary(api_url: str, cookie: str, since: datetime, until: datetime) -> dict | None:
    import httpx

    params = {"from": since.date().isoformat(), "to": (until - timedelta(seconds=1)).date().isoformat()}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{api_url.rstrip('/')}/stats/summary", params=params, headers={"Cookie": cookie})
        r.raise_for_status()
        return r.json()


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
    try:
        conn = await asyncpg.connect(dsn)
    except Exception as exc:  # noqa: BLE001
        print(f"нет соединения с Postgres: {exc}", file=sys.stderr)
        return 2
    try:
        events = _row_map(await conn.fetch(_EVENTS_SQL, since, until))
        hourly = _row_map(await conn.fetch(_HOURLY_SQL, since, until))
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
        summary = await fetch_api_summary(args.api_url, args.cookie, since, until)
        api = {
            "impressions": float(summary.get("impressions", 0)),
            "clicks": float(summary.get("clicks", 0)),
            "spend": float(summary.get("spend", 0)),
        }
        api_bad = {
            m: {"stats_hourly": report["stats_hourly"][m], "api": api[m]}
            for m in api
            if abs(api[m] - report["stats_hourly"][m]) > (args.tolerance_spend if m == "spend" else args.tolerance_rows)
        }
        report["api_summary"] = api
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
        if "api_summary" in report:
            print("api /stats/summary:", report["api_summary"], "diff:", report["api_diff"] or "нет")
        if diffs:
            print(f"\nРАСХОЖДЕНИЯ в {len(diffs)} час(ах):")
            for d in diffs[:20]:
                print(" ", d)
        print("\nPASS" if report["pass"] else "\nFAIL")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
