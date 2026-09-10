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
    "rtb_wins_total": "Win notifications accepted (CPM: billed; CPC: journal only).",
    "rtb_win_spend_rub_sum": "Billed spend (RUB): CPM win notices + CPC billed clicks.",
    "rtb_cpc_wins_total": "Win notifications of CPC bids (not billed; the click is).",
    "rtb_cpc_clicks_billed_total": "First clicks of CPC bids that were billed (audit R-03).",
    "rtb_nobid_fx_rate_missing_total": "Candidates skipped: no CBR rate for the floor/response currency (R-03).",
    "rtb_imps_total": "Impression events accepted.",
    "rtb_viewable_imps_total": "Viewable impression events (MRC 50%/1s) accepted.",
    "rtb_clicks_total": "Click events accepted.",
    "rtb_event_token_rejects_total": "Event notifications rejected (bad/expired token).",
    # Consumer событий Go-биддера (events_consumer.py): rtb:events -> bid_events/stats_hourly.
    "rtb_events_consumed_total": "Bid/loss events consumed from the Go bidder Redis stream.",
    "rtb_events_consumer_errors_total": "Consumer iterations that failed (Redis/PG error, retried with backoff).",
    "rtb_events_claimed_total": "Stale pending events taken over from other consumers via XAUTOCLAIM (E-03).",
    "rtb_test_wins_total": "Win notices for OpenRTB test=1 bids (recorded, not billed).",
    "ord_export_failures_total": "Monthly ORD stats exports that ended with status=failed.",
    # Reservation lifecycle (audit M-05/M-06): исход перевода резерва в clearing на win.
    "rtb_win_settle_converted_total": "Wins whose bid-time reservation was converted to the clearing price.",
    "rtb_win_settle_recharged_total": "Late wins re-charged after the reservation had been released (loss/expire).",
    "rtb_win_settle_duplicate_total": "Win settle calls for bids already in state win (no money moved).",
    "rtb_settle_win_errors_total": "settle_win Redis failures after a successful DB bill (reconcile fixes drift).",
    # R-07: billing by burl. Wins of billing_event=burl partners hold the budget reservation until the burl.
    "rtb_win_pending_burl_total": "Win notices of billing_event=burl partners: reservation kept, bid queued in pending:burl (R-07).",
    "rtb_win_pending_unreserved_total": "Wins queued in pending:burl whose reservation had already been released (loss/expire before the nurl).",
    "rtb_win_pending_errors_total": "win_pending Redis failures after the win event was committed (reservation may be released by the bidder-go sweeper).",
    "rtb_burl_total": "Billing notices (burl) that charged money (billing_event=burl partners, non-test, non-CPC).",
    "rtb_burl_duplicate_total": "Billing notices for bids already billed (no money moved).",
    "rtb_burl_late_total": "Billing notices that arrived after the pending:burl sweep had expired the win: charged once, event flagged late (R-07).",
    "rtb_burl_in_win_mode_total": "Billing notices from billing_event=win partners: ignored (money was charged on the win); the partner is a candidate for burl.",
    "rtb_burl_without_nurl_total": "Billing notices with no prior win notice for the bid (partner sends burl only).",
    "rtb_win_without_burl_total": "Won bids of billing_event=burl partners whose burl never came within BURL_TTL: reservation released, no spend, event win_expired (R-07).",
    "rtb_burl_expire_reserve_missing_total": "pending:burl expirations that found the reservation already gone (bidder-go sweeper reached the bid first: raise BURL_SWEEP_GRACE or cron frequency).",
    "rtb_freq_win_without_imp_total": "Won bids whose frequency-cap reservation expired without an imp pixel (R-06); high share = broken SSP pixel.",
    "rtb_imp_late_after_reservation_total": "Imp pixels that arrived after the bid's frequency-cap reservation had expired (WIN_IMP_TTL) or without a prior win (R-06); rising together with rtb_freq_win_without_imp_total means the SSP pixel is slow, not broken — raise WIN_IMP_TTL.",
    "rtb_imp_freq_uncounted_total": "Impressions that could not be committed to the frequency cap (no bidmeta fdev/fcaps, e.g. PG fallback).",
    "rtb_imp_freq_errors_total": "confirm_imp Redis errors on the imp pixel (R-06).",
    "audience_reseeded_segments_total": "Audience segments whose audz: ZSET was re-seeded from Postgres by /cron/audience-sync (Redis had lost it).",
    "reserve_reconcile_drift_total": "avail:* keys whose value drifted from DB balance minus outstanding reserves.",
    "csrf_rejected_total": "State-changing requests rejected by the Origin guard (S-01).",
}

# Gauges (D8): значения, которые нельзя вывести из счётчиков. None = ещё не
# измерялось в этом процессе -> серия не рендерится (в PromQL видно как absent()).
_GAUGE_HELP = {
    "bid_events_default_partition_rows": (
        "Rows sitting in bid_events DEFAULT partition (should be 0; >0 = partition cron lagged). "
        "Refreshed by GET /cron/retention."
    ),
    "ord_export_last_success_timestamp_seconds": (
        "Unix time of the last successful monthly ORD export (seeded from ord_export_log at startup)."
    ),
    "ord_export_skipped_without_erid": (
        "Creatives with impressions in the last exported period but no registered erid "
        "(should be 0; >0 = ads served without ORD marking). Refreshed by GET /cron/ord-export."
    ),
    "ord_export_rows_rejected": (
        "Statistic rows the ORD provider rejected (or did not acknowledge) in the last export run; "
        "log status partial/failed, per-row errors in ord_export_rows. Refreshed by GET /cron/ord-export."
    ),
    # Consumer событий Go-биддера (E-03). Стрим ограничен MAXLEN ~200k на стороне
    # Go: lag, приближающийся к длине стрима, означает скорую потерю событий.
    "rtb_events_stream_length": "Entries currently in the rtb:events Redis stream (XLEN).",
    "rtb_events_pending": "Delivered-but-unacked entries in the backend consumer group (PEL size).",
    "rtb_events_lag": "Entries in rtb:events not yet delivered to the backend group (XINFO GROUPS lag).",
    # R-07: очередь выигрышей, ждущих burl. Обновляется GET /cron/sweep-burl.
    "rtb_pending_burl_size": "Won bids of billing_event=burl partners still waiting for their burl (ZCARD pending:burl).",
    "rtb_pending_burl_oldest_age_seconds": "Age of the oldest win still waiting for its burl (0 when the queue is empty).",
}

# R-07: исходы биллинга по burl per SSP — для агрегированного алерта
# WinWithoutBurlRateHigh (доля expired к won по партнёру за окно), а не на каждый
# единичный win без burl. Label partner = supply_partners.id (bounded: партнёров
# десятки); outcome — фиксированный список.
BURL_OUTCOMES = ("won", "billed", "late", "expired")

# Фиксированный список cron-задач (routers/cron.py) — bounded label `job`.
CRON_JOBS = (
    "rules",
    "reset_daily",
    "optimize",
    "fx_refresh",
    "shading",
    "ml_train",
    "domain_quality",
    "ord_export",
    "retention",
    "reconcile_reserves",
    "audience_sync",
    "sweep_burl",
)
_CRON_RESULTS = ("ok", "error")

# Классы HTTP-ответов бэкенда (доля 5xx для алерта Backend5xxShare).
HTTP_CLASSES = ("1xx", "2xx", "3xx", "4xx", "5xx")

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

# Причины несовместимости кандидата с OpenRTB-слотом (audit R-04): кандидат
# пропущен явно, а не отдан заведомо невалидной ставкой. Строки — те же, что в
# Go (rtb/openrtb.go, bidder_openrtb_incompat_total).
OPENRTB_INCOMPAT_REASONS = (
    "native_request_malformed",
    "native_asset_missing",
    "video_mime",
    "video_protocol",
    "video_duration",
    "private_auction",
    # Audit R-05: разметку не собрать безопасно (URL креатива / пользовательский VAST).
    "adm_url_invalid",
    "adm_vast_malformed",
)

# F-02/B14c: конверсии, которые нельзя привязать к биду (label status), и
# покрытие атрибуции по измерениям отчёта (gauge, обновляется /cron/optimize).
UNATTRIBUTED_STATUSES = ("out_of_window", "unattributed")
COVERAGE_DIMENSIONS = (
    "campaign", "creative", "supply_partner", "domain", "device", "country",
    "os", "browser", "ad_type", "tagid", "publisher", "bundle",
)

_lock = threading.Lock()
_started_at = time.time()

_counters: dict[str, float] = {name: 0.0 for name in _COUNTER_HELP}
_no_bid: dict[str, int] = {r: 0 for r in NO_BID_REASONS}
_prefilter: dict[str, int] = {r: 0 for r in PREFILTER_REASONS}
_openrtb_incompat: dict[str, int] = {r: 0 for r in OPENRTB_INCOMPAT_REASONS}
_lat_counts = [0] * (len(_LATENCY_BUCKETS) + 1)
_lat_sum = 0.0
_lat_total = 0
_gauges: dict[str, float | None] = {name: None for name in _GAUGE_HELP}
_cron_runs: dict[tuple[str, str], int] = {(j, r): 0 for j in CRON_JOBS for r in _CRON_RESULTS}
_cron_last_ok: dict[str, float | None] = {j: None for j in CRON_JOBS}
_http: dict[str, int] = {c: 0 for c in HTTP_CLASSES}
_conv_unattributed: dict[str, int] = {s: 0 for s in UNATTRIBUTED_STATUSES}
_coverage: dict[str, float | None] = {d: None for d in COVERAGE_DIMENSIONS}
_burl_outcomes: dict[tuple[str, str], int] = {}


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


def inc_openrtb_incompat(reason: str) -> None:
    """Кандидат пропущен: несовместим с OpenRTB-слотом (audit R-04).

    Неизвестная причина — KeyError (fail fast: список общий с Go)."""
    if reason not in _openrtb_incompat:
        raise KeyError(f"unknown openrtb incompat reason: {reason}")
    with _lock:
        _openrtb_incompat[reason] += 1


def inc_conversion_unattributed(status: str) -> None:
    """Конверсия без атрибуции к биду (F-02). Неизвестный статус — KeyError."""
    if status not in _conv_unattributed:
        raise KeyError(f"unknown attribution status: {status}")
    with _lock:
        _conv_unattributed[status] += 1


def inc_burl_outcome(partner_id: str | None, outcome: str) -> None:
    """Исход биллинга по burl для SSP (R-07). Неизвестный outcome — KeyError."""
    if outcome not in BURL_OUTCOMES:
        raise KeyError(f"unknown burl outcome: {outcome}")
    key = (str(partner_id or "unknown"), outcome)
    with _lock:
        _burl_outcomes[key] = _burl_outcomes.get(key, 0) + 1


def set_coverage(dimension: str, ratio: float | None) -> None:
    """Покрытие атрибуции по измерению отчёта, 0..1; None — конверсий нет (серия не рендерится)."""
    if dimension not in _coverage:
        raise KeyError(f"unknown coverage dimension: {dimension}")
    with _lock:
        _coverage[dimension] = None if ratio is None else float(ratio)


def set_gauge(name: str, value: float) -> None:
    """Установить gauge. Неизвестное имя — KeyError (fail fast на опечатку)."""
    if name not in _GAUGE_HELP:
        raise KeyError(f"unknown metric gauge: {name}")
    with _lock:
        _gauges[name] = float(value)


def record_cron_run(job: str, ok: bool, now: float | None = None) -> None:
    """Исход запуска cron-задачи; job вне CRON_JOBS — KeyError."""
    if job not in CRON_JOBS:
        raise KeyError(f"unknown cron job: {job}")
    ts = time.time() if now is None else now
    with _lock:
        _cron_runs[(job, "ok" if ok else "error")] += 1
        if ok:
            _cron_last_ok[job] = ts


def inc_http_status(status_code: int) -> None:
    """Класс ответа бэкенда (1xx..5xx); всё вне диапазона — 5xx (ошибка сервера)."""
    cls = f"{status_code // 100}xx"
    key = cls if cls in _http else "5xx"
    with _lock:
        _http[key] += 1


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
            "openrtb_incompat": dict(_openrtb_incompat),
            "bid_latency": {"count": _lat_total, "sum": _lat_sum, "buckets": buckets},
            "gauges": dict(_gauges),
            "cron_runs": dict(_cron_runs),
            "cron_last_ok": dict(_cron_last_ok),
            "http": dict(_http),
            "burl_outcomes": dict(_burl_outcomes),
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

        lines.append("# HELP rtb_openrtb_incompat_total Candidates skipped as incompatible with the OpenRTB slot (R-04).")
        lines.append("# TYPE rtb_openrtb_incompat_total counter")
        for reason in OPENRTB_INCOMPAT_REASONS:
            lines.append(f'rtb_openrtb_incompat_total{{reason="{reason}"}} {_openrtb_incompat[reason]}')

        lines.append("# HELP conversions_unattributed_total Pixel conversions that could not be attributed to a bid (F-02): out_of_window = bid older than PIXEL_ATTRIBUTION_WINDOW_DAYS, unattributed = no/foreign clid.")
        lines.append("# TYPE conversions_unattributed_total counter")
        for st in UNATTRIBUTED_STATUSES:
            lines.append(f'conversions_unattributed_total{{status="{st}"}} {_conv_unattributed[st]}')

        lines.append("# HELP conversion_attribution_coverage Share of conversions (7d, all owners) that land in a row of the report dimension: attributed AND dimension not NULL. < 0.8 = conversion KPIs unreliable.")
        lines.append("# TYPE conversion_attribution_coverage gauge")
        for d in COVERAGE_DIMENSIONS:
            if _coverage[d] is not None:
                lines.append(f'conversion_attribution_coverage{{dimension="{d}"}} {_coverage[d]}')

        lines.append("# HELP rtb_burl_outcomes_total Billing-by-burl outcomes per SSP (R-07): won = win notice queued for burl, billed = burl charged in time, late = burl after expire (charged once), expired = burl never came (no spend).")
        lines.append("# TYPE rtb_burl_outcomes_total counter")
        for (partner, outcome) in sorted(_burl_outcomes):
            lines.append(f'rtb_burl_outcomes_total{{partner="{partner}",outcome="{outcome}"}} {_burl_outcomes[(partner, outcome)]}')

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

        lines.append("# HELP http_responses_total Backend HTTP responses by status class.")
        lines.append("# TYPE http_responses_total counter")
        for cls in HTTP_CLASSES:
            lines.append(f'http_responses_total{{class="{cls}"}} {_http[cls]}')

        lines.append("# HELP cron_job_runs_total Cron endpoint runs by job and result.")
        lines.append("# TYPE cron_job_runs_total counter")
        for job in CRON_JOBS:
            for res in _CRON_RESULTS:
                lines.append(f'cron_job_runs_total{{job="{job}",result="{res}"}} {_cron_runs[(job, res)]}')

        lines.append("# HELP cron_job_last_success_timestamp_seconds Unix time of the last successful run per job (this process).")
        lines.append("# TYPE cron_job_last_success_timestamp_seconds gauge")
        for job in CRON_JOBS:
            ts = _cron_last_ok[job]
            if ts is not None:
                lines.append(f'cron_job_last_success_timestamp_seconds{{job="{job}"}} {ts:.3f}')

        for name in sorted(_GAUGE_HELP):
            value = _gauges[name]
            if value is None:
                continue
            lines.append(f"# HELP {name} {_GAUGE_HELP[name]}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value:g}")

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
        for r in _openrtb_incompat:
            _openrtb_incompat[r] = 0
        for i in range(len(_lat_counts)):
            _lat_counts[i] = 0
        _lat_sum = 0.0
        _lat_total = 0
        for name in _gauges:
            _gauges[name] = None
        for key in _cron_runs:
            _cron_runs[key] = 0
        for job in _cron_last_ok:
            _cron_last_ok[job] = None
        for cls in _http:
            _http[cls] = 0
        for st in _conv_unattributed:
            _conv_unattributed[st] = 0
        for d in _coverage:
            _coverage[d] = None
        _burl_outcomes.clear()
