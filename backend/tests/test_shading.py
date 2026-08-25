"""Тесты математики bid-шейдинга: клампы, обратная связь, EMA, мин. данные."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shading import compute_raw_factor, MIN_FACTOR


def test_insufficient_data_returns_one():
    # < 50 бидов — не шейдим вслепую
    assert compute_raw_factor(avg_ratio=0.5, bids=49, wins=20) == 1.0
    # нет винов — нечего учить
    assert compute_raw_factor(avg_ratio=0.5, bids=1000, wins=0) == 1.0


def test_basic_shading_math():
    # ratio 0.60, win_rate 0.40 (без обратной связи): raw = 0.65, EMA с prev=1.0 -> 0.825
    f = compute_raw_factor(avg_ratio=0.60, bids=100, wins=40, prev=1.0)
    assert abs(f - 0.825) < 1e-9


def test_low_win_rate_bumps_factor_up():
    # win_rate 0.10 < 0.20 -> raw = 0.65 + 0.05 = 0.70, EMA -> 0.85
    f = compute_raw_factor(avg_ratio=0.60, bids=100, wins=10, prev=1.0)
    assert abs(f - 0.85) < 1e-9


def test_high_win_rate_shades_deeper():
    # win_rate 0.70 > 0.60 -> raw = 0.65 - 0.03 = 0.62, EMA -> 0.81
    f = compute_raw_factor(avg_ratio=0.60, bids=100, wins=70, prev=1.0)
    assert abs(f - 0.81) < 1e-9


def test_factor_never_below_min():
    # экстремально низкий ratio: клампится в MIN_FACTOR даже после EMA
    f = compute_raw_factor(avg_ratio=0.01, bids=1000, wins=700, prev=MIN_FACTOR)
    assert f >= MIN_FACTOR


def test_factor_never_above_one():
    f = compute_raw_factor(avg_ratio=0.99, bids=1000, wins=100, prev=1.0)
    assert f <= 1.0


def test_ema_smoothing_converges():
    # повторное применение с тем же raw сходится к raw
    prev = 1.0
    for _ in range(12):
        prev = compute_raw_factor(avg_ratio=0.60, bids=100, wins=40, prev=prev)
    assert abs(prev - 0.65) < 0.01


def test_shaded_price_respects_floor():
    # инвариант горячего пути: price = max(floor * 1.01, price * factor)
    floor, price, factor = 2.0, 5.0, 0.55
    shaded = max(floor * 1.01, price * factor)
    assert shaded >= floor
    # глубокий шейдинг упирается в флор
    floor2, price2 = 4.9, 5.0
    shaded2 = max(floor2 * 1.01, price2 * 0.55)
    assert shaded2 == floor2 * 1.01 and shaded2 > floor2
