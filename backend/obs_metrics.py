"""Prometheus-метрики RTB-пути (text format 0.0.4) без внешних зависимостей.

Зеркалит подход bidder-go/internal/metrics: bounded-лейблы, фиксированная
таксономия причин, никаких auction id / URL / user id в лейблах.
Все операции под threading.Lock — безопасно и для anyio worker-threads.

Экспозиция: GET /metrics в main.py.
"""
from __future__ import annotations

import threading
import time

# Фиксированная таксономия причин no-bid (подмножество совпадает с Go-биддером).
# Неизвестные причины схлопываются в "other" — кардинальность не растёт.
NO_BID_REASONS = (
    "python_serving_off",
    "parse_error",
    "no_candidates",
    "currency",
    "other",
)

# Единственные допустимые имена счётчиков — защита от опечаток и роста метрик.
_COUNTER_HELP = {
    "rtb_bid_requests_total": "Incoming OpenRTB bid requests (Python path).",
    "rtb_bid_responses_total": "Responses containing at least one bid.",
    "rtb_bids_total": "Individual bids submitted.",
    "rtb_bid_price_cpm_rub_sum": "Sum of submitted bid prices (RUB CPM).",
    "rtb_wins_total": "Win notifications accepted and billed.",
    "rtb_win_spend_rub_sum": "Billed spend from win notices (RUB).",
    "rtb_imps_total": "Impression events accepted.",
    "rtb_viewable_imps_total": "Viewable impression events (MRC 50%/1s) accepted.",
    "rtb_clicks_total": "Click events accepted.",
    "rtb_event_token_rejects_total": "Event notifications rejected (bad/expired token).",
    # Consumer событий Go-биддера (events_consumer.py): rtb:events -> bid_events/stats_hourly.
    "rtb_events_consumed_total": "Bid/loss events consumed from the Go bidder Redis stream.",
    "rtb_events_consumer_errors_total": "Consumer iterations that failed (Redis/PG error, retried with backoff).",
    "rtb_test_wins_total": "Win notices for OpenRTB test=1 bids (recorded, not billed).",
}

_LATENCY_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 1.0)

# Таксономия причин throttling-предфильтра (Часть C). ОТДЕЛЬНО от no-bid:
# prefilter-дропы не доходят до полного цикла — метрика измеряет экономию инфраструктуры.
PREFILTER_REASONS = (
    "bot_ua",
    "geo_unserved",
    "no_device_id",
    "datacenter_ip",
    "other",
)

_lock = threading.Lock()
_started_at = time.time()

_counters: dict[str, float] = {name: 0.0 for name in _COUNTER_HELP}
_no_bid: dict[str, int] = {r: 0 for r in NO_BID_REASONS}
_prefilter: dict[str, int] = {r: 0 for r in PREFILTER_REASONS}
_lat_counts = [0] * (len(_LATENCY_BUCKETS) + 1)
_lat_sum = 0.0
_lat_total = 0


def inc(name: str, value: float = 1.0) -> None:
    """Инкремент счётчика. Неизвестное имя — KeyError (fail fast на опечатку)."""
    if name not in _COUNTER_HELP:
        raise KeyError(f"unknown metric counter: {name}")
    with _lock:
        _counters[name] += value


def inc_no_bid(reason: str) -> None:
    """No-bid по причине; причины вне таксономии считаются как 'other'."""
    key = reason if reason in _no_bid else "other"
    with _lock:
        _no_bid[key] += 1


def inc_prefilter(reason: str) -> None:
    """Запрос отсечён throttling-слоем до полного цикла (Часть C)."""
    key = reason if reason in _prefilter else "other"
    with _lock:
        _prefilter[key] += 1


def observe_bid_latency(sec: float) -> None:
    global _lat_sum, _lat_total
    idx = len(_LATENCY_BUCKETS)
    for i, b in enumerate(_LATENCY_BUCKETS):
        if sec <= b:
            idx = i
            break
    with _lock:
        _lat_counts[idx] += 1
        _lat_sum += sec
        _lat_total += 1


def snapshot() -> dict:
    """Копия текущего состояния (для тестов и диагностики)."""
    with _lock:
        cum = 0
        buckets: list[tuple[float, int]] = []
        for i, b in enumerate(_LATENCY_BUCKETS):
            cum += _lat_counts[i]
            buckets.append((b, cum))
        return {
            "counters": dict(_counters),
            "no_bid": dict(_no_bid),
            "prefilter": dict(_prefilter),
            "bid_latency": {"count": _lat_total, "sum": _lat_sum, "buckets": buckets},
        }


def render_prometheus() -> str:
    """Скрейп в Prometheus text exposition format 0.0.4."""
    with _lock:
        lines: list[str] = []
        for name in sorted(_COUNTER_HELP):
            lines.append(f"# HELP {name} {_COUNTER_HELP[name]}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {_counters[name]:g}")

        lines.append("# HELP rtb_no_bid_total No-bid outcomes by reason.")
        lines.append("# TYPE rtb_no_bid_total counter")
        for reason in NO_BID_REASONS:
            lines.append(f'rtb_no_bid_total{{reason="{reason}"}} {_no_bid[reason]}')

        lines.append("# HELP rtb_prefilter_drops_total Requests dropped by throttling pre-filter (before full cycle).")
        lines.append("# TYPE rtb_prefilter_drops_total counter")
        for reason in PREFILTER_REASONS:
            lines.append(f'rtb_prefilter_drops_total{{reason="{reason}"}} {_prefilter[reason]}')

        lines.append("# HELP rtb_bid_latency_seconds Bid request handling latency.")
        lines.append("# TYPE rtb_bid_latency_seconds histogram")
        cum = 0
        for i, b in enumerate(_LATENCY_BUCKETS):
            cum += _lat_counts[i]
            lines.append(f'rtb_bid_latency_seconds_bucket{{le="{b:g}"}} {cum}')
        cum += _lat_counts[-1]
        lines.append(f'rtb_bid_latency_seconds_bucket{{le="+Inf"}} {cum}')
        lines.append(f"rtb_bid_latency_seconds_sum {_lat_sum:g}")
        lines.append(f"rtb_bid_latency_seconds_count {_lat_total}")

        lines.append("# HELP process_uptime_seconds Seconds since process start.")
        lines.append("# TYPE process_uptime_seconds gauge")
        lines.append(f"process_uptime_seconds {time.time() - _started_at:g}")
        return "\n".join(lines) + "\n"


# Обратная совместимость: старое имя рендера.
render = render_prometheus


def reset_for_tests() -> None:
    """Только для тестов."""
    global _lat_sum, _lat_total
    with _lock:
        for name in _counters:
            _counters[name] = 0.0
        for r in _no_bid:
            _no_bid[r] = 0
        for r in _prefilter:
            _prefilter[r] = 0
        for i in range(len(_lat_counts)):
            _lat_counts[i] = 0
        _lat_sum = 0.0
        _lat_total = 0
