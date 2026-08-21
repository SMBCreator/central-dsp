"""Load test for the RTB bid endpoint: QPS ramp + latency percentiles.

No external tooling (k6/vegeta) required - pure asyncio + httpx, so it runs
in CI or any dev box. Sends realistic OpenRTB 2.5 banner requests.

Usage:
    python loadtest/rtb_load.py --url http://localhost:8080/rtb/bid/SEAT \
        --token PARTNER_TOKEN --qps 100 --seconds 30

Pass/fail: exits 1 when p99 > --p99-budget-ms (default 80).
"""
import argparse
import asyncio
import json
import random
import statistics
import sys
import time
import uuid

import httpx


def make_bid_request() -> dict:
    return {
        "id": str(uuid.uuid4()),
        "imp": [{
            "id": "1",
            "banner": {"w": 300, "h": 250},
            "bidfloor": round(random.uniform(0.1, 1.0), 2),
            "bidfloorcur": "RUB",
        }],
        "site": {"id": "site-1", "domain": "example.ru", "page": "https://example.ru/news"},
        "device": {
            "ua": "Mozilla/5.0 (load-test)",
            "ip": f"10.0.{random.randint(0, 255)}.{random.randint(1, 254)}",
            "os": random.choice(["Android", "iOS", "Windows"]),
            "devicetype": random.choice([1, 2, 4]),
            "ifa": str(uuid.uuid4()),
        },
        "user": {"id": str(uuid.uuid4())},
        "at": 1,
        "tmax": 120,
        "cur": ["RUB"],
    }


async def worker(client, url, headers, latencies, statuses, stop_at, interval, record_after):
    while time.monotonic() < stop_at:
        started = time.monotonic()
        try:
            resp = await client.post(url, json=make_bid_request(), headers=headers)
            if started >= record_after:
                latencies.append((time.monotonic() - started) * 1000)
                statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
        except Exception:
            if started >= record_after:
                statuses["error"] = statuses.get("error", 0) + 1
        elapsed = time.monotonic() - started
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--token", default="")
    p.add_argument("--qps", type=int, default=100)
    p.add_argument("--seconds", type=int, default=30)
    p.add_argument("--concurrency", type=int, default=50)
    p.add_argument("--p99-budget-ms", type=float, default=80.0)
    p.add_argument("--warmup-seconds", type=int, default=5,
                   help="ramp-up period excluded from latency stats (TLS/pool warmup)")
    args = p.parse_args()

    headers = {"Content-Type": "application/json"}
    if args.token:
        headers["x-centraldsp-token"] = args.token

    latencies: list[float] = []
    statuses: dict = {}
    record_after = time.monotonic() + args.warmup_seconds
    stop_at = record_after + args.seconds
    # Each worker paces itself so aggregate ~= target QPS.
    interval = args.concurrency / args.qps

    async with httpx.AsyncClient(timeout=5.0) as client:
        await asyncio.gather(*[
            worker(client, args.url, headers, latencies, statuses, stop_at, interval, record_after)
            for _ in range(args.concurrency)
        ])

    if not latencies:
        print("No successful requests recorded", file=sys.stderr)
        sys.exit(1)

    latencies.sort()
    total = len(latencies)
    p50 = latencies[int(total * 0.50)]
    p95 = latencies[int(total * 0.95)]
    p99 = latencies[min(int(total * 0.99), total - 1)]
    report = {
        "requests": total,
        "achieved_qps": round(total / args.seconds, 1),
        "latency_ms": {"p50": round(p50, 1), "p95": round(p95, 1), "p99": round(p99, 1),
                       "mean": round(statistics.mean(latencies), 1)},
        "status_codes": statuses,
        "p99_budget_ms": args.p99_budget_ms,
        "pass": p99 <= args.p99_budget_ms,
    }
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    asyncio.run(main())
