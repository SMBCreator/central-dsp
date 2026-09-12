"""loadtest/rtb_load.py: вердикт нельзя получить ложно-положительным.

Аудит 2026-09-06 (O-04): старый генератор считал 403/429/500 «успешными
сэмплами» и печатал PASS. Здесь каждый класс ответа прогоняется через
run_worker с httpx.MockTransport (без сети, workers=1 в процессе) и через
чистую verdict() на синтетических агрегатах для случаев, которые нельзя
воспроизвести транспортом (насыщение генератора).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "loadtest"))
import rtb_load as rl  # noqa: E402


def cfg(**kw) -> rl.Config:
    base = dict(url="http://bidder/rtb/bid/key", qps=200, seconds=1, warmup_seconds=0, workers=1, tmax_ms=120)
    base.update(kw)
    return rl.Config(**base)


def run(c: rl.Config, handler) -> dict:
    res = asyncio.run(rl.run_worker(c, 0, transport=httpx.MockTransport(handler)))
    return rl.verdict(c, rl.aggregate(c, [res]))


def bid_response_for(request: httpx.Request, *, price=1.5, id_override=None) -> httpx.Response:
    req = json.loads(request.content)
    body = {"id": id_override or req["id"], "cur": "RUB",
            "seatbid": [{"bid": [{"id": "b1", "impid": req["imp"][0]["id"], "price": price, "adm": "<div/>"}]}]}
    return httpx.Response(200, json=body)


def test_all_nobid_204_passes_with_nobid_warning():
    r = run(cfg(), lambda req: httpx.Response(204))
    assert r["result"] == "PASS" and r["exit_code"] == 0
    assert r["responses"]["nobid_204"] > 0 and r["bid_rate"] == 0
    assert any("только 204" in w for w in r["warnings"])
    assert any("test=1" in w for w in r["warnings"])


def test_valid_bid_responses_pass_and_bid_rate_1():
    r = run(cfg(min_bid_rate=0.9), bid_response_for)
    assert r["result"] == "PASS"
    assert r["bid_rate"] == 1.0 and r["responses"]["invalid_200"] == 0


def test_all_403_is_config_error_not_pass():
    r = run(cfg(), lambda req: httpx.Response(403, text="bad token"))
    assert r["result"] == "CONFIG" and r["exit_code"] == 2
    assert "403" in r["reason"]


def test_all_404_is_config_error():
    r = run(cfg(), lambda req: httpx.Response(404, text="unknown endpoint"))
    assert r["exit_code"] == 2 and "404" in r["reason"]


def test_any_429_fails_separate_gate():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(429) if n["i"] % 20 == 0 else httpx.Response(204)

    r = run(cfg(), handler)
    assert r["result"] == "FAIL" and r["exit_code"] == 1
    assert r["gates"]["no_429"]["pass"] is False
    assert r["gates"]["error_rate"]["pass"] is True, "429 — отдельный gate, не часть error budget"


def test_expect_rate_limit_mode_requires_429_and_does_not_claim_capacity():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(429) if n["i"] % 2 == 0 else httpx.Response(204)

    r = run(cfg(expect_rate_limit=120), handler)
    assert r["result"] == "PASS" and r["gates"]["rate_limit_fires"]["pass"] is True
    assert any("capacity" in w for w in r["warnings"])
    r2 = run(cfg(expect_rate_limit=120), lambda req: httpx.Response(204))
    assert r2["result"] == "FAIL", "лимитер не сработал — режим проверки лимитера провален"


def test_5xx_over_budget_fails():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(500) if n["i"] % 50 == 0 else httpx.Response(204)

    r = run(cfg(), handler)
    assert r["result"] == "FAIL" and r["gates"]["error_rate"]["pass"] is False


def test_timeouts_count_as_errors_and_enter_p99_as_tmax():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] % 10 == 0:
            raise httpx.ReadTimeout("slow bidder", request=req)
        return httpx.Response(204)

    r = run(cfg(), handler)
    assert r["result"] == "FAIL"
    assert r["responses"]["timeout"] > 0
    assert r["latency_ms"]["p99"] == 120.0, "timeout учитывается как tmax, а не выпадает из перцентилей"


def test_invalid_bid_response_fails_validation_gate():
    r = run(cfg(), lambda req: bid_response_for(req, id_override="other-id"))
    assert r["result"] == "FAIL"
    assert r["gates"]["valid_bid_responses"]["pass"] is False
    assert r["responses"]["invalid_200"] > 0 and r["invalid_examples"]


def test_zero_price_bid_is_invalid():
    r = run(cfg(), lambda req: bid_response_for(req, price=0))
    assert r["gates"]["valid_bid_responses"]["pass"] is False


def test_connection_refused_everywhere_is_config():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    r = run(cfg(), handler)
    assert r["exit_code"] == 2 and "недостижим" in r["reason"]


def _synthetic(c: rl.Config, *, planned, sent, not_sent=0, nobid=None, timeout=0, lag_ms=0.5, cpu=0.3) -> dict:
    nobid = sent - timeout if nobid is None else nobid
    t = rl.Tally(c.tmax_ms, max(c.lag_budget * 10, 1000.0))
    t.c.update(planned=planned, sent=sent, not_sent_inflight_cap=not_sent, nobid=nobid, timeout=timeout)
    for _ in range(nobid):
        t.latency.add(2.0); t.latency_ok.add(2.0)
    for _ in range(timeout):
        t.latency.add(c.tmax_ms)
    for _ in range(planned):
        t.lag.add(lag_ms)
    d = t.to_dict()
    d["generator"] = {"worker": 0, "cpu_seconds": cpu, "wall_seconds": 1.0, "cpu_util": cpu, "max_rss_mb": 50}
    return d


def test_generator_saturation_is_not_tested_exit_3():
    c = cfg(qps=35000, seconds=1)
    agg = rl.aggregate(c, [_synthetic(c, planned=35000, sent=18200, not_sent=16800, lag_ms=50, cpu=0.99)])
    r = rl.verdict(c, agg)
    assert r["result"] == "NOT TESTED" and r["exit_code"] == 3
    assert r["target_qps"] == 35000 and r["achieved_qps"] == 18200.0
    assert r["generator"]["limited"] is True
    assert "не результат биддера" in r["reason"]


def test_underload_with_bidder_errors_is_fail_not_not_tested():
    c = cfg(qps=1000, seconds=1)
    # генератор держит расписание (lag мал, cpu низкий), но 40% запросов не ушли из-за
    # cap и половина ушедших — timeout: биддер не справляется
    agg = rl.aggregate(c, [_synthetic(c, planned=1000, sent=600, not_sent=400, timeout=300, lag_ms=0.5, cpu=0.2)])
    r = rl.verdict(c, agg)
    assert r["result"] == "FAIL" and r["exit_code"] == 1


def test_hist_percentiles():
    h = rl.Hist(100)
    for ms in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
        h.add(ms)
    assert h.percentile(50) == 5.1 and h.percentile(99) == 10.1
    h.add(500)  # выше max -> последний bucket = max
    assert h.percentile(100) == 100.0
    assert rl.Hist(10).percentile(99) is None


@pytest.mark.parametrize("body,expected", [
    (b"not json", "not json"),
    (b"[]", "not an object"),
    (b'{"id":"x","seatbid":[]}', "empty seatbid on HTTP 200"),
])
def test_validate_bid_response_reasons(body, expected):
    req = {"id": "x", "imp": [{"id": "1"}], "cur": ["RUB"]}
    assert rl.validate_bid_response(req, body) == expected


def test_parse_args_headers_and_token():
    c = rl.parse_args(["--url", "http://b/x", "--qps", "10", "--seconds", "1", "--token", "T",
                       "--header", "X-CentralDSP-Internal-Check: R", "--no-test-flag", "--workers", "3"])
    assert c.headers == {"X-CentralDSP-Internal-Check": "R", rl.TOKEN_HEADER: "T"}
    assert c.test_flag is False and c.workers == 3 and c.interval == pytest.approx(0.3)


def test_bad_args_exit_2(capsys):
    assert rl.main(["--url", "http://b/x", "--qps", "0", "--seconds", "1"]) == 2
    assert rl.main(["--url", "http://b/x", "--qps", "1", "--seconds", "1", "--header", "no-colon"]) == 2
