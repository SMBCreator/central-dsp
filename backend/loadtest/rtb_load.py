"""Open-loop нагрузочный генератор для POST /rtb/bid/{endpoint_key} Go-биддера.

    python loadtest/rtb_load.py --url http://bidder-go:8090/rtb/bid/KEY \
        --token PARTNER_TOKEN --qps 35000 --seconds 120 --workers 8 \
        --tmax-ms 120 --p99-budget-ms 30

Чем отличается от closed-loop «N корутин по кругу»:
  * offered load задаётся расписанием: каждый worker шлёт запрос строго в
    момент t0 + k*interval, не дожидаясь предыдущего ответа. Медленный биддер
    не снижает нагрузку (нет coordinated omission), а растёт in-flight.
  * timeout = tmax: ответ после tmax SSP не примет, поэтому он считается
    нарушением и входит в p99 как tmax (не выпадает из перцентилей).
  * --workers N — отдельные OS-процессы (один asyncio-процесс даёт ~2-5k QPS).
    Итог агрегируется; отчёт содержит target vs achieved QPS, отставание от
    расписания и CPU самого генератора, чтобы отличать «биддер не тянет» от
    «генератор не тянет».

Вердикт и exit code (stdout — JSON-отчёт, stderr — краткая сводка):
  0 PASS        achieved >= --min-achieved-ratio * target, 429 = 0,
                error_rate <= --max-error-rate, p99 <= --p99-budget-ms,
                все 200-ответы — валидные BidResponse, bid_rate >= --min-bid-rate
  1 FAIL        нагрузка подана, но хотя бы один gate не выполнен (в т.ч. любой 429:
                QPS-гейт партнёра сработал — система не приняла заявленную нагрузку)
  2 CONFIG      ошибка использования/окружения: все ответы 403 (auth) или 404
                (endpoint), URL недостижим с первого запроса, неверные аргументы
  3 NOT TESTED  generator не создал требуемую нагрузку (упёрся в CPU/сокеты/
                расписание) — это НЕ результат производительности биддера

Режим проверки лимитера (--expect-rate-limit LIMIT): 429 ожидаются; PASS, если
они есть и accepted QPS <= LIMIT*1.1. Такой прогон НЕ подтверждает capacity.

OpenRTB test=1 (по умолчанию): биддер отвечает бидом, но не резервирует бюджет
и не списывает деньги (is_test в событиях). Путь Redis-резерва при этом не
нагружается — отчёт это указывает. --no-test-flag шлёт боевые запросы.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import multiprocessing as mp
import os
import random
import resource
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field

import httpx

EXIT_PASS, EXIT_FAIL, EXIT_CONFIG, EXIT_NOT_TESTED = 0, 1, 2, 3
TOKEN_HEADER = "X-CentralDSP-Token"
HIST_RES_MS = 0.1


@dataclass
class Config:
    url: str
    qps: int
    seconds: int
    warmup_seconds: int = 5
    workers: int = 1
    tmax_ms: int = 120
    headers: dict = field(default_factory=dict)
    test_flag: bool = True
    max_inflight: int = 0  # 0 = авто: 4 * qps_per_worker * tmax
    p99_budget_ms: float = 30.0
    max_error_rate: float = 0.001
    min_achieved_ratio: float = 0.95
    min_bid_rate: float = 0.0
    expect_rate_limit: float | None = None
    lag_budget_ms: float = 0.0  # 0 = авто: max(5, 2*interval)

    @property
    def interval(self) -> float:
        return self.workers / self.qps

    @property
    def inflight_cap(self) -> int:
        if self.max_inflight > 0:
            return self.max_inflight
        return max(64, math.ceil(self.qps / self.workers * self.tmax_ms / 1000 * 4))

    @property
    def lag_budget(self) -> float:
        return self.lag_budget_ms or max(5.0, 2 * self.interval * 1000)


class Hist:
    """Гистограмма фиксированного шага HIST_RES_MS до max_ms; значения выше —
    в последнем bucket (для latency это tmax = timeout)."""

    def __init__(self, max_ms: float, counts: list[int] | None = None):
        self.max_ms = max_ms
        self.n_buckets = int(round(max_ms / HIST_RES_MS)) + 1
        self.counts = counts if counts is not None else [0] * self.n_buckets

    def add(self, ms: float) -> None:
        idx = int(ms / HIST_RES_MS)
        self.counts[min(max(idx, 0), self.n_buckets - 1)] += 1

    def merge(self, other: "Hist") -> None:
        for i, c in enumerate(other.counts):
            self.counts[i] += c

    @property
    def total(self) -> int:
        return sum(self.counts)

    def percentile(self, p: float) -> float | None:
        total = self.total
        if total == 0:
            return None
        rank = math.ceil(p / 100 * total)
        acc = 0
        for i, c in enumerate(self.counts):
            acc += c
            if acc >= rank:
                return round(min((i + 1) * HIST_RES_MS, self.max_ms), 1)
        return self.max_ms

    def mean(self) -> float | None:
        total = self.total
        if total == 0:
            return None
        return round(sum((i + 0.5) * HIST_RES_MS * c for i, c in enumerate(self.counts)) / total, 2)


COUNTER_KEYS = ("planned", "sent", "not_sent_inflight_cap", "bid", "nobid", "429", "4xx", "5xx",
                "timeout", "conn_error", "invalid_response", "other_2xx")


def make_bid_request(test_flag: bool, tmax_ms: int) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "test": 1 if test_flag else 0,
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
        "tmax": tmax_ms,
        "cur": ["RUB"],
    }


def validate_bid_response(req: dict, body: bytes) -> str | None:
    """None, если тело — валидный OpenRTB BidResponse на этот запрос; иначе причина."""
    try:
        resp = json.loads(body)
    except ValueError:
        return "not json"
    if not isinstance(resp, dict):
        return "not an object"
    if resp.get("id") != req["id"]:
        return f"id mismatch: {resp.get('id')!r}"
    seatbids = resp.get("seatbid")
    if not isinstance(seatbids, list) or not seatbids:
        return "empty seatbid on HTTP 200"
    cur = resp.get("cur")
    if req.get("cur") and cur not in req["cur"]:
        return f"cur {cur!r} not accepted by request"
    imp_ids = {imp["id"] for imp in req["imp"]}
    for sb in seatbids:
        bids = sb.get("bid") if isinstance(sb, dict) else None
        if not isinstance(bids, list) or not bids:
            return "seatbid without bids"
        for b in bids:
            if not isinstance(b, dict) or not b.get("id"):
                return "bid without id"
            if b.get("impid") not in imp_ids:
                return f"bid.impid {b.get('impid')!r} not in request"
            price = b.get("price")
            if not isinstance(price, (int, float)) or not price > 0:
                return f"bid.price invalid: {price!r}"
            if not b.get("adm") and not b.get("nurl"):
                return "bid without adm and nurl"
    return None


class Tally:
    def __init__(self, tmax_ms: int, lag_max_ms: float):
        self.c = dict.fromkeys(COUNTER_KEYS, 0)
        self.latency = Hist(tmax_ms)           # все ответы + timeout как tmax
        self.latency_ok = Hist(tmax_ms)        # только 200/204
        self.lag = Hist(lag_max_ms)            # отставание отправки от расписания
        self.status_other: dict[str, int] = {}
        self.invalid_examples: list[str] = []
        self.first_status: int | None = None

    def to_dict(self) -> dict:
        return {
            "counters": self.c, "latency": self.latency.counts, "latency_ok": self.latency_ok.counts,
            "lag": self.lag.counts, "status_other": self.status_other,
            "invalid_examples": self.invalid_examples, "first_status": self.first_status,
        }


async def _one_request(client: httpx.AsyncClient, cfg: Config, tally: Tally, record: bool) -> None:
    req = make_bid_request(cfg.test_flag, cfg.tmax_ms)
    body = json.dumps(req, separators=(",", ":")).encode()
    t0 = time.monotonic()
    try:
        resp = await client.post(cfg.url, content=body)
        ms = (time.monotonic() - t0) * 1000
        status = resp.status_code
        if tally.first_status is None:
            tally.first_status = status
        if not record:
            return
        tally.latency.add(ms)
        if status == 200:
            tally.latency_ok.add(ms)
            reason = validate_bid_response(req, resp.content)
            if reason is None:
                tally.c["bid"] += 1
            else:
                tally.c["invalid_response"] += 1
                if len(tally.invalid_examples) < 3:
                    tally.invalid_examples.append(f"{reason}: {resp.content[:120]!r}")
        elif status == 204:
            tally.latency_ok.add(ms)
            tally.c["nobid"] += 1
        elif status == 429:
            tally.c["429"] += 1
        elif 200 <= status < 300:
            tally.c["other_2xx"] += 1
            tally.status_other[str(status)] = tally.status_other.get(str(status), 0) + 1
        elif 400 <= status < 500:
            tally.c["4xx"] += 1
            tally.status_other[str(status)] = tally.status_other.get(str(status), 0) + 1
        else:
            tally.c["5xx"] += 1
            tally.status_other[str(status)] = tally.status_other.get(str(status), 0) + 1
    except httpx.TimeoutException:
        if record:
            tally.c["timeout"] += 1
            tally.latency.add(cfg.tmax_ms)  # нарушение SLO входит в перцентили как tmax
    except (httpx.HTTPError, OSError) as e:
        if tally.first_status is None:
            tally.first_status = 0
        if record:
            tally.c["conn_error"] += 1
            tally.latency.add(cfg.tmax_ms)
            key = f"conn:{type(e).__name__}"
            tally.status_other[key] = tally.status_other.get(key, 0) + 1


async def run_worker(cfg: Config, worker_idx: int, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Один open-loop процесс. transport — для тестов (httpx.MockTransport)."""
    interval = cfg.interval
    lag_max = max(cfg.lag_budget * 10, 1000.0)
    tally = Tally(cfg.tmax_ms, lag_max)
    ru0 = resource.getrusage(resource.RUSAGE_SELF)
    wall0 = time.monotonic()
    # Worker'ы сдвинуты по фазе, чтобы суммарный поток был равномерным.
    t_start = wall0 + 0.05 + worker_idx * interval / cfg.workers
    record_after = t_start + cfg.warmup_seconds
    stop_at = record_after + cfg.seconds
    limits = httpx.Limits(max_connections=cfg.inflight_cap, max_keepalive_connections=cfg.inflight_cap)
    timeout = httpx.Timeout(cfg.tmax_ms / 1000)
    headers = {"Content-Type": "application/json", **cfg.headers}
    inflight: set[asyncio.Task] = set()
    recorded: set[asyncio.Task] = set()
    async with httpx.AsyncClient(timeout=timeout, limits=limits, headers=headers, transport=transport) as client:
        k = 0
        while True:
            planned = t_start + k * interval
            if planned >= stop_at:
                break
            now = time.monotonic()
            if planned > now:
                await asyncio.sleep(planned - now)
                now = time.monotonic()
            record = planned >= record_after
            if record:
                tally.c["planned"] += 1
                tally.lag.add((now - planned) * 1000)
            if len(inflight) >= cfg.inflight_cap:
                if record:
                    tally.c["not_sent_inflight_cap"] += 1
            else:
                if record:
                    tally.c["sent"] += 1
                task = asyncio.ensure_future(_one_request(client, cfg, tally, record))
                inflight.add(task)
                task.add_done_callback(inflight.discard)
                if record:
                    recorded.add(task)
                    task.add_done_callback(recorded.discard)
            k += 1
        if inflight:
            _, pending = await asyncio.wait(inflight, timeout=cfg.tmax_ms / 1000 + 1.0)
            for t in pending:
                t.cancel()
                if t in recorded:  # незавершённые запросы окна измерения = timeout
                    tally.c["timeout"] += 1
                    tally.latency.add(cfg.tmax_ms)
    ru1 = resource.getrusage(resource.RUSAGE_SELF)
    wall = time.monotonic() - wall0
    out = tally.to_dict()
    out["generator"] = {
        "worker": worker_idx,
        "cpu_seconds": round((ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime), 3),
        "wall_seconds": round(wall, 3),
        "cpu_util": round(((ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime)) / wall, 3) if wall > 0 else 0,
        "max_rss_mb": round(ru1.ru_maxrss / 1024, 1),
    }
    return out


def _worker_entry(args: tuple[dict, int]) -> dict:
    cfg_dict, idx = args
    return asyncio.run(run_worker(Config(**cfg_dict), idx))


def aggregate(cfg: Config, results: list[dict]) -> dict:
    counters = dict.fromkeys(COUNTER_KEYS, 0)
    status_other: dict[str, int] = {}
    lat = Hist(cfg.tmax_ms)
    lat_ok = Hist(cfg.tmax_ms)
    lag_max = max(cfg.lag_budget * 10, 1000.0)
    lag = Hist(lag_max)
    invalid: list[str] = []
    gens = []
    first_statuses = []
    for r in results:
        for k in COUNTER_KEYS:
            counters[k] += r["counters"][k]
        for k, v in r["status_other"].items():
            status_other[k] = status_other.get(k, 0) + v
        lat.merge(Hist(cfg.tmax_ms, r["latency"]))
        lat_ok.merge(Hist(cfg.tmax_ms, r["latency_ok"]))
        lag.merge(Hist(lag_max, r["lag"]))
        invalid.extend(r["invalid_examples"][: 3 - len(invalid)])
        gens.append(r["generator"])
        first_statuses.append(r.get("first_status"))
    return {
        "counters": counters, "status_other": status_other, "latency": lat, "latency_ok": lat_ok,
        "lag": lag, "invalid_examples": invalid, "generators": gens, "first_statuses": first_statuses,
    }


def verdict(cfg: Config, agg: dict) -> dict:
    """Чистая функция: агрегат -> отчёт с result/exit_code/reason/gates."""
    c = agg["counters"]
    sent = c["sent"]
    responses = c["bid"] + c["nobid"] + c["429"] + c["4xx"] + c["5xx"] + c["other_2xx"] + c["invalid_response"]
    completed = responses + c["timeout"] + c["conn_error"]
    accepted = c["bid"] + c["nobid"] + c["invalid_response"]
    errors = c["4xx"] + c["5xx"] + c["timeout"] + c["conn_error"] + c["other_2xx"]
    sec = cfg.seconds
    achieved_qps = round(sent / sec, 1) if sec else 0.0
    accepted_qps = round(accepted / sec, 1) if sec else 0.0
    ratio = achieved_qps / cfg.qps if cfg.qps else 0.0
    error_rate = round(errors / sent, 5) if sent else 0.0
    bid_rate = round(c["bid"] / accepted, 4) if accepted else 0.0
    gens = agg["generators"]
    cpu_util_max = max((g["cpu_util"] for g in gens), default=0.0)
    lag_p95 = agg["lag"].percentile(95)
    p50, p95, p99 = (agg["latency"].percentile(p) for p in (50, 95, 99))

    report = {
        "url": cfg.url, "openrtb_test_flag": cfg.test_flag, "workers": cfg.workers,
        "target_qps": cfg.qps, "seconds": sec, "warmup_seconds": cfg.warmup_seconds, "tmax_ms": cfg.tmax_ms,
        "planned": c["planned"], "sent": sent, "not_sent_inflight_cap": c["not_sent_inflight_cap"],
        "completed": completed, "achieved_qps": achieved_qps, "achieved_ratio": round(ratio, 3),
        "accepted_qps": accepted_qps,
        "responses": {"bid_200": c["bid"], "nobid_204": c["nobid"], "invalid_200": c["invalid_response"],
                      "429": c["429"], "4xx": c["4xx"], "5xx": c["5xx"], "other_2xx": c["other_2xx"],
                      "timeout": c["timeout"], "conn_error": c["conn_error"], "by_status": agg["status_other"]},
        "error_rate": error_rate, "bid_rate": bid_rate,
        "latency_ms": {"p50": p50, "p95": p95, "p99": p99, "mean": agg["latency"].mean(),
                       "note": "все ответы; timeout/conn_error учтены как tmax"},
        "latency_accepted_ms": {"p50": agg["latency_ok"].percentile(50), "p95": agg["latency_ok"].percentile(95),
                                "p99": agg["latency_ok"].percentile(99)},
        "generator": {"send_lag_p95_ms": lag_p95, "send_lag_p99_ms": agg["lag"].percentile(99),
                      "cpu_util_max": cpu_util_max, "cpu_seconds_total": round(sum(g["cpu_seconds"] for g in gens), 2),
                      "max_rss_mb": max((g["max_rss_mb"] for g in gens), default=0), "host_cpus": os.cpu_count(),
                      "per_worker": gens},
        "invalid_examples": agg["invalid_examples"],
        "thresholds": {"p99_budget_ms": cfg.p99_budget_ms, "max_error_rate": cfg.max_error_rate,
                       "min_achieved_ratio": cfg.min_achieved_ratio, "min_bid_rate": cfg.min_bid_rate,
                       "expect_rate_limit": cfg.expect_rate_limit, "lag_budget_ms": round(cfg.lag_budget, 1)},
        "gates": {}, "warnings": [],
    }
    if cfg.test_flag:
        report["warnings"].append("test=1: биддер не резервирует бюджет и не списывает деньги — путь Redis-резерва не нагружен")

    def finish(result: str, code: int, reason: str) -> dict:
        report.update({"result": result, "exit_code": code, "reason": reason})
        return report

    if sent == 0:
        report["generator"]["limited"] = c["not_sent_inflight_cap"] > 0
        if c["not_sent_inflight_cap"] > 0:
            return finish("NOT TESTED", EXIT_NOT_TESTED,
                          f"генератор насыщен: все {c['planned']} запросов окна не отправлены — потолок in-flight "
                          f"{cfg.inflight_cap} на worker занят (send_lag_p95={lag_p95}ms, cpu_util_max={cpu_util_max}); "
                          "это не результат биддера: добавьте --workers или генераторы")
        return finish("NOT TESTED", EXIT_NOT_TESTED, "ни одного запроса не отправлено (окно измерения пусто)")
    if completed and completed == c["conn_error"]:
        return finish("CONFIG", EXIT_CONFIG, f"ни одного HTTP-ответа: {agg['status_other']} — URL недостижим")
    only = {k for k, v in agg["status_other"].items() if v > 0}
    if responses and responses == c["4xx"] and only <= {"403"}:
        return finish("CONFIG", EXIT_CONFIG, "все ответы 403 — токен партнёра/IP whitelist/internal check отвергнуты; нагрузка не измерена")
    if responses and responses == c["4xx"] and only <= {"404"}:
        return finish("CONFIG", EXIT_CONFIG, "все ответы 404 — неверный endpoint_key или партнёр не active/testing")

    generator_limited = (lag_p95 is not None and lag_p95 > cfg.lag_budget) or cpu_util_max > 0.85 \
        or (c["not_sent_inflight_cap"] > 0 and error_rate <= cfg.max_error_rate)
    report["generator"]["limited"] = generator_limited
    if ratio < cfg.min_achieved_ratio:
        detail = (f"target={cfg.qps} achieved={achieved_qps} QPS ({ratio:.0%}); send_lag_p95={lag_p95}ms, "
                  f"cpu_util_max={cpu_util_max}, not_sent(cap)={c['not_sent_inflight_cap']}, error_rate={error_rate}")
        if error_rate > cfg.max_error_rate and not generator_limited:
            return finish("FAIL", EXIT_FAIL, "offered load не достигнут и биддер отвечал ошибками/таймаутами: " + detail)
        return finish("NOT TESTED", EXIT_NOT_TESTED,
                      "генератор не создал требуемую нагрузку (saturated) — это не результат биддера: " + detail)

    g = report["gates"]
    g["achieved_qps"] = {"pass": True, "value": achieved_qps, "min": round(cfg.qps * cfg.min_achieved_ratio, 1)}
    if cfg.expect_rate_limit is None:
        g["no_429"] = {"pass": c["429"] == 0, "value": c["429"]}
    else:
        lim = cfg.expect_rate_limit
        g["rate_limit_fires"] = {"pass": c["429"] > 0 and accepted_qps <= lim * 1.1, "value_429": c["429"],
                                 "accepted_qps": accepted_qps, "limit": lim}
        report["warnings"].append("режим проверки лимитера: capacity НЕ подтверждается этим прогоном")
    g["error_rate"] = {"pass": error_rate <= cfg.max_error_rate, "value": error_rate, "max": cfg.max_error_rate}
    g["p99_ms"] = {"pass": p99 is not None and p99 <= cfg.p99_budget_ms, "value": p99, "max": cfg.p99_budget_ms}
    g["valid_bid_responses"] = {"pass": c["invalid_response"] == 0, "invalid": c["invalid_response"]}
    g["bid_rate"] = {"pass": bid_rate >= cfg.min_bid_rate, "value": bid_rate, "min": cfg.min_bid_rate}
    if c["bid"] == 0:
        report["warnings"].append("реальных бидов нет (только 204) — прогон покрывает путь no-bid, не аукцион; "
                                  "для capacity аукциона нужна тестовая кампания и --min-bid-rate")
    failed = [k for k, v in g.items() if not v["pass"]]
    if failed:
        return finish("FAIL", EXIT_FAIL, "не выполнены gates: " + ", ".join(f"{k}={g[k]}" for k in failed))
    return finish("PASS", EXIT_PASS, "все gates выполнены")


def parse_args(argv: list[str]) -> Config:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True)
    p.add_argument("--token", default="", help=f"открытый токен партнёра -> заголовок {TOKEN_HEADER}")
    p.add_argument("--header", action="append", default=[], help="доп. заголовок 'Name: value' (можно несколько)")
    p.add_argument("--qps", type=int, required=True, help="offered load суммарно по всем workers")
    p.add_argument("--seconds", type=int, required=True, help="окно измерения после warmup")
    p.add_argument("--warmup-seconds", type=int, default=5)
    p.add_argument("--workers", type=int, default=1, help="OS-процессов генератора (1 = в этом процессе)")
    p.add_argument("--tmax-ms", type=int, default=120, help="tmax в запросе и timeout клиента")
    p.add_argument("--max-inflight", type=int, default=0, help="потолок одновременных запросов на worker (0 = авто)")
    p.add_argument("--p99-budget-ms", type=float, default=30.0)
    p.add_argument("--max-error-rate", type=float, default=0.001, help="доля 4xx/5xx/timeout/conn_error от отправленных")
    p.add_argument("--min-achieved-ratio", type=float, default=0.95, help="achieved/target, ниже — NOT TESTED")
    p.add_argument("--min-bid-rate", type=float, default=0.0, help="доля 200 среди 200+204; 0 = не проверять")
    p.add_argument("--expect-rate-limit", type=float, default=None, metavar="LIMIT_QPS",
                   help="режим проверки QPS-лимитера: ждём 429 и accepted<=LIMIT*1.1")
    p.add_argument("--no-test-flag", action="store_true", help="OpenRTB test=0: боевые запросы с резервом и списанием")
    a = p.parse_args(argv)
    if a.qps <= 0 or a.seconds <= 0 or a.workers <= 0:
        p.error("--qps, --seconds, --workers должны быть > 0")
    headers = {}
    for h in a.header:
        name, sep, value = h.partition(":")
        if not sep:
            p.error(f"--header ожидает 'Name: value', получено {h!r}")
        headers[name.strip()] = value.strip()
    if a.token:
        headers[TOKEN_HEADER] = a.token
    return Config(url=a.url, qps=a.qps, seconds=a.seconds, warmup_seconds=a.warmup_seconds, workers=a.workers,
                  tmax_ms=a.tmax_ms, headers=headers, test_flag=not a.no_test_flag, max_inflight=a.max_inflight,
                  p99_budget_ms=a.p99_budget_ms, max_error_rate=a.max_error_rate,
                  min_achieved_ratio=a.min_achieved_ratio, min_bid_rate=a.min_bid_rate,
                  expect_rate_limit=a.expect_rate_limit)


def run(cfg: Config) -> dict:
    if cfg.workers == 1:
        results = [asyncio.run(run_worker(cfg, 0))]
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(cfg.workers) as pool:
            results = pool.map(_worker_entry, [(asdict(cfg), i) for i in range(cfg.workers)])
    return verdict(cfg, aggregate(cfg, results))


def main(argv: list[str] | None = None) -> int:
    try:
        cfg = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as e:  # argparse
        return EXIT_CONFIG if e.code else 0
    report = run(cfg)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    r = report["responses"]
    print(f"TARGET: {cfg.qps} QPS  ACHIEVED: {report['achieved_qps']} QPS  ACCEPTED: {report['accepted_qps']} QPS\n"
          f"200={r['bid_200']} 204={r['nobid_204']} 429={r['429']} 4xx={r['4xx']} 5xx={r['5xx']} "
          f"timeout={r['timeout']} conn_error={r['conn_error']} invalid={r['invalid_200']}\n"
          f"p50/p95/p99 = {report['latency_ms']['p50']}/{report['latency_ms']['p95']}/{report['latency_ms']['p99']} ms  "
          f"error_rate={report['error_rate']} bid_rate={report['bid_rate']}\n"
          f"generator: send_lag_p95={report['generator']['send_lag_p95_ms']}ms cpu_util_max={report['generator']['cpu_util_max']}\n"
          f"RESULT: {report['result']}  EXIT: {report['exit_code']}\nREASON: {report['reason']}",
          file=sys.stderr)
    for w in report["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
