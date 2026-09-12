"""Тесты ML-слоя (Часть B): pCTR-предсказание и скоринг качества доменов.

Тренировка (train/recompute) требует Postgres и покрывается интеграционно;
здесь — чистая математика предсказания, guardrails value-множителя и
таксономия причин качества. Модель подсовывается через тестовые швы модулей.
"""

import math

import pytest

from ml import pctr, quality


# ------------------------------------------------------------------ predict --

@pytest.mark.asyncio
async def test_predict_none_without_model():
    """Нет модели -> None -> биддер работает по базовой цене (fail-safe)."""
    pctr.reset_cache_for_tests()

    class BrokenPool:
        async def fetchrow(self, *a, **kw):
            raise RuntimeError("db down")

    assert await pctr.predict(BrokenPool(), domain="a.ru", creative_id="c1",
                              device_type="mobile", hour=12) is None


@pytest.mark.asyncio
async def test_predict_prior_when_no_segments_match():
    pctr.set_cache_for_tests(7, 0.02, {})
    res = await pctr.predict(None, domain="unknown.ru", creative_id="nope",
                             device_type="tv", hour=3)
    assert res is not None
    assert res["matched"] == 0
    assert res["pctr"] == pytest.approx(0.02)
    assert res["baseline"] == pytest.approx(0.02)
    assert res["version"] == 7


@pytest.mark.asyncio
async def test_predict_segment_shifts_logit():
    """Сегмент с CTR выше глобального повышает pCTR, ниже — понижает."""
    pctr.set_cache_for_tests(1, 0.01, {
        ("domain", "good.ru"): 0.03,
        ("domain", "bad.ru"): 0.003,
    })
    up = await pctr.predict(None, domain="good.ru", creative_id=None,
                            device_type=None, hour=None)
    down = await pctr.predict(None, domain="bad.ru", creative_id=None,
                              device_type=None, hour=None)
    assert up["matched"] == 1 and down["matched"] == 1
    assert up["pctr"] > 0.01 > down["pctr"]


@pytest.mark.asyncio
async def test_predict_combines_multiple_segments():
    pctr.set_cache_for_tests(1, 0.01, {
        ("domain", "good.ru"): 0.03,
        ("device_type", "mobile"): 0.02,
        ("hour", "20"): 0.015,
    })
    res = await pctr.predict(None, domain="good.ru", creative_id=None,
                             device_type="mobile", hour=20)
    assert res["matched"] == 3
    # Все сегменты «выше глобального» — комбинированный pCTR выше одиночного
    single = await pctr.predict(None, domain="good.ru", creative_id=None,
                                device_type=None, hour=None)
    assert res["pctr"] > single["pctr"]
    assert 0.0 < res["pctr"] < 1.0


@pytest.mark.asyncio
async def test_predict_domain_case_insensitive():
    pctr.set_cache_for_tests(1, 0.01, {("domain", "good.ru"): 0.03})
    res = await pctr.predict(None, domain="GOOD.RU", creative_id=None,
                             device_type=None, hour=None)
    assert res["matched"] == 1


# ------------------------------------------------- value-множитель (guardrail)

def _clamp_mult(pctr_val: float, baseline: float) -> float:
    """Копия формулы из rtb.py: множитель зажат в [0.7, 1.3]."""
    return min(1.3, max(0.7, pctr_val / baseline))


def test_multiplier_clamped_up():
    assert _clamp_mult(0.10, 0.01) == 1.3   # 10x сегмент -> максимум +30%


def test_multiplier_clamped_down():
    assert _clamp_mult(0.001, 0.01) == 0.7  # 0.1x сегмент -> максимум -30%


def test_multiplier_neutral():
    assert _clamp_mult(0.01, 0.01) == pytest.approx(1.0)


# ------------------------------------------------------------------ quality --

def test_score_domain_penalize_ctr_anomaly():
    """CTR во много раз выше глобального -> низкий скор, причина ctr_anomaly."""
    score, reasons = quality.score_domain(
        imps=10_000, clicks=1_900, wins=10_200, global_ctr=0.012)
    assert score < quality.CLEAN_SCORE_THRESHOLD
    assert "ctr_anomaly" in reasons


def test_score_domain_penalize_click_burst():
    """Кликов больше показов — невозможная последовательность."""
    score, reasons = quality.score_domain(
        imps=100, clicks=350, wins=120, global_ctr=0.012)
    assert "click_burst" in reasons
    assert score < quality.CLEAN_SCORE_THRESHOLD


def test_score_domain_penalize_zero_engage():
    score, reasons = quality.score_domain(
        imps=8_000, clicks=0, wins=8_100, global_ctr=0.012)
    assert "zero_engage" in reasons
    assert score < 1.0


def test_score_domain_penalize_imp_no_win():
    """Показов сильно больше win-ов -> replay или дубли пикселя."""
    score, reasons = quality.score_domain(
        imps=9_000, clicks=110, wins=1_000, global_ctr=0.012)
    assert "imp_no_win" in reasons


def test_score_domain_healthy():
    score, reasons = quality.score_domain(
        imps=50_000, clicks=600, wins=52_000, global_ctr=0.012)
    assert score >= quality.CLEAN_SCORE_THRESHOLD
    assert reasons == []


def test_score_domain_bounded():
    for imps, clicks, wins in [(0, 0, 0), (1, 1, 1), (10**7, 10**6, 10**5)]:
        score, _ = quality.score_domain(
            imps=imps, clicks=clicks, wins=wins, global_ctr=0.012)
        assert 0.0 <= score <= 1.0
        assert math.isfinite(score)


# --------------------------------------------------- blocked domains cache ---

@pytest.mark.asyncio
async def test_get_blocked_domains_fail_open():
    """Postgres недоступен -> пустой set, закупка не останавливается."""
    quality.reset_cache_for_tests()

    class BrokenPool:
        async def fetch(self, *a, **kw):
            raise RuntimeError("db down")

    blocked = await quality.get_blocked_domains(BrokenPool())
    assert blocked == frozenset()


@pytest.mark.asyncio
async def test_get_blocked_domains_uses_cache():
    quality.set_blocked_for_tests({"Bad.RU", "farm.ru"})
    blocked = await quality.get_blocked_domains(None)  # pool не тронется — кэш свежий
    assert "bad.ru" in blocked and "farm.ru" in blocked
