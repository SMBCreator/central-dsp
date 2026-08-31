"""Тесты чистой логики скоринга качества площадок (Часть 3b)."""
import scoring


def _profile(**over):
    base = {
        "min_ctr": 0.05,
        "min_viewability": 60.0,
        "max_bounce_rate": 70.0,
        "min_avg_depth": 1.5,
        "min_time_on_site": 20,
        "max_cpa": 1500.0,
        "block_threshold": 0.5,
    }
    base.update(over)
    return base


def test_clean_domain_scores_one():
    """Домен, проходящий все пороги, получает score=1.0 без причин."""
    m = {"ctr": 1.2, "viewability": 72, "bounce_rate": 40, "avg_depth": 3.1, "avg_time": 55, "cpa": 700}
    score, reasons = scoring.score_domain(_profile(), m)
    assert score == 1.0
    assert reasons == []


def test_low_ctr_and_viewability_blocks():
    """Клик-фрод: аномальный CTR обычно идёт с низким viewability — двойной штраф топит домен."""
    m = {"ctr": 0.01, "viewability": 33, "bounce_rate": 40, "avg_depth": 3, "avg_time": 55, "cpa": 700}
    score, reasons = scoring.score_domain(_profile(), m)
    assert "low_ctr" in reasons and "low_viewability" in reasons
    assert score == 0.0  # 1 - 0.5 - 0.5


def test_high_cpa_alone_blocks():
    """Дорогая конверсия сама по себе опускает домен ниже дефолтного порога."""
    m = {"ctr": 1.2, "viewability": 72, "bounce_rate": 40, "avg_depth": 3, "avg_time": 55, "cpa": 5000}
    score, reasons = scoring.score_domain(_profile(), m)
    assert reasons == ["high_cpa"]
    assert score == pytest_approx(0.4)
    assert score < 0.5  # flagged


def test_disabled_rules_are_skipped():
    """Пороги None выключены — даже плохие метрики не штрафуются."""
    prof = _profile(min_ctr=None, min_viewability=None, max_bounce_rate=None,
                    min_avg_depth=None, min_time_on_site=None, max_cpa=None)
    m = {"ctr": 0.0, "viewability": 0, "bounce_rate": 100, "avg_depth": 1, "avg_time": 0, "cpa": 99999}
    score, reasons = scoring.score_domain(prof, m)
    assert score == 1.0
    assert reasons == []


def test_missing_metric_skips_rule():
    """Нет данных по метрике (None) — правило не срабатывает (fail-open)."""
    m = {"ctr": 1.2, "viewability": 72, "bounce_rate": None, "avg_depth": None, "avg_time": None, "cpa": None}
    score, reasons = scoring.score_domain(_profile(), m)
    assert score == 1.0
    assert reasons == []


def test_behavior_penalties_accumulate():
    """Плохое поведение (отказы + мелкая глубина + мало времени) складывается."""
    m = {"ctr": 1.2, "viewability": 72, "bounce_rate": 95, "avg_depth": 1.0, "avg_time": 3, "cpa": 700}
    score, reasons = scoring.score_domain(_profile(), m)
    assert set(reasons) == {"high_bounce", "low_depth", "low_time"}
    assert score == pytest_approx(0.05)  # 1 - 0.35 - 0.3 - 0.3


def test_score_never_negative():
    """Множество нарушений не уводит score ниже нуля."""
    m = {"ctr": 0, "viewability": 0, "bounce_rate": 100, "avg_depth": 0, "avg_time": 0, "cpa": 99999}
    score, _ = scoring.score_domain(_profile(), m)
    assert score == 0.0


def pytest_approx(v):
    import pytest
    return pytest.approx(v, abs=1e-9)
