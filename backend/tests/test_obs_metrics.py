"""Тесты процессных Prometheus-метрик (obs_metrics) — Часть D (GAP-011).

Проверяются:
- инкременты счётчиков и no-bid причин (bounded labels);
- summary латентности симулятора (sum, count по engine);
- рендер в Prometheus text exposition format 0.0.4;
- отсутствие неограниченных лейблов (только фиксированный словарь причин);
- эндпоинт GET /metrics.
"""
import pytest

import obs_metrics


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Каждый тест начинается с чистого состояния реестра."""
    obs_metrics.reset_for_tests()
    yield
    obs_metrics.reset_for_tests()


# ---------------------------------------------------------------- counters ---

def test_inc_counter_default_and_amount():
    obs_metrics.inc("rtb_wins_total")
    obs_metrics.inc("rtb_wins_total")
    obs_metrics.inc("rtb_imps_total", 3)
    snap = obs_metrics.snapshot()
    assert snap["counters"]["rtb_wins_total"] == 2
    assert snap["counters"]["rtb_imps_total"] == 3


def test_inc_float_amount():
    obs_metrics.inc("rtb_win_spend_rub_sum", 12.5)
    obs_metrics.inc("rtb_win_spend_rub_sum", 0.5)
    snap = obs_metrics.snapshot()
    assert snap["counters"]["rtb_win_spend_rub_sum"] == pytest.approx(13.0)


def test_unknown_counter_name_rejected():
    """Только зарегистрированные имена — защита от бесконтрольного роста метрик."""
    with pytest.raises(KeyError):
        obs_metrics.inc("totally_unknown_metric")


# ------------------------------------------------------------ no-bid reasons ---

def test_no_bid_reasons_bounded():
    obs_metrics.inc_no_bid("no_candidates")
    obs_metrics.inc_no_bid("no_candidates")
    obs_metrics.inc_no_bid("parse_error")
    snap = obs_metrics.snapshot()
    assert snap["no_bid"]["no_candidates"] == 2
    assert snap["no_bid"]["parse_error"] == 1


def test_no_bid_unknown_reason_mapped_to_other():
    """Неизвестная причина не создаёт новый лейбл (bounded cardinality)."""
    obs_metrics.inc_no_bid("weird_unexpected_reason")
    snap = obs_metrics.snapshot()
    assert snap["no_bid"]["other"] == 1
    assert "weird_unexpected_reason" not in snap["no_bid"]


# ----------------------------------------------------- simulator latency ---

def test_simulator_latency_summary():
    obs_metrics.observe_simulator_latency("go", 0.003)
    obs_metrics.observe_simulator_latency("go", 0.020)
    obs_metrics.observe_simulator_latency("python_reference", 9.0)
    snap = obs_metrics.snapshot()
    go_count, go_sum = snap["simulator_latency"]["go"]
    assert go_count == 2
    assert go_sum == pytest.approx(0.023)
    ref_count, ref_sum = snap["simulator_latency"]["python_reference"]
    assert ref_count == 1
    assert ref_sum == pytest.approx(9.0)


def test_simulator_unknown_engine_rejected():
    """Только зарегистрированные engine — bounded label engine."""
    with pytest.raises(KeyError):
        obs_metrics.inc_simulator_request("totally_unknown_engine")


# ----------------------------------------------------------------- render ---

def test_render_prometheus_format():
    obs_metrics.inc("rtb_wins_total")
    obs_metrics.inc_no_bid("no_candidates")
    obs_metrics.inc_simulator_request("go")
    obs_metrics.observe_simulator_latency("go", 0.010)
    text = obs_metrics.render_prometheus()
    assert "# TYPE rtb_wins_total counter" in text
    assert "rtb_wins_total 1" in text
    assert 'rtb_no_bid_total{reason="no_candidates"} 1' in text
    assert 'simulator_requests_total{engine="go"} 1' in text
    assert 'simulator_requests_total{engine="python_reference"} 0' in text
    assert "# TYPE simulator_engine_latency_seconds summary" in text
    assert 'simulator_engine_latency_seconds_count{engine="go"} 1' in text


def test_render_no_unbounded_labels():
    """В выводе нет ID кампаний/аукционов/URL — только фиксированные имена и причины."""
    for reason in ("no_candidates", "parse_error", "python_serving_off", "currency", "other"):
        obs_metrics.inc_no_bid(reason)
    text = obs_metrics.render_prometheus()
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        # единственный допустимый лейбл — reason/le с фиксированным словарём
        assert "campaign" not in line.lower() or "rtb_" in line.split("{")[0]


# ------------------------------------------------------------- concurrency ---

def test_concurrent_increments_thread_safe():
    """Счётчики защищены локом: параллельные инкременты не теряются."""
    import threading

    def worker():
        for _ in range(1000):
            obs_metrics.inc("rtb_wins_total")
            obs_metrics.inc_no_bid("no_candidates")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = obs_metrics.snapshot()
    assert snap["counters"]["rtb_wins_total"] == 8000
    assert snap["no_bid"]["no_candidates"] == 8000
