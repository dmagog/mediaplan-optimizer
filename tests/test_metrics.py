"""Агрегация метрик: считаем ли мы MAPE, WAPE и прочее так, как обещаем в отчёте.

Постановка требует тестов на агрегацию метрик отдельно от прогонов, поэтому здесь
проверяется сама арифметика на числах, которые можно посчитать в уме, и то, что
функции не падают на вырожденном входе: нулевой план, пустой список, идеальное
совпадение факта с планом.
"""

import numpy as np
import pytest

from harness.metrics import (
    coefficient_of_variation,
    final_deviation,
    mape,
    unsmoothness,
    wape,
)


def test_mape_is_zero_when_fact_equals_plan():
    plan = np.linspace(1.0, 100.0, 100)
    assert mape(plan, plan.copy()) == 0.0


def test_mape_counts_relative_error_and_skips_warmup():
    """Первые часы кампании не считаются: план там близок к нулю и любая ошибка выглядит огромной."""
    plan = np.full(100, 10.0)
    fact = plan.copy()
    fact[:5] = 1000.0  # выброс в прогреве
    assert mape(plan, fact) == 0.0
    fact = np.full(100, 11.0)
    assert mape(plan, fact) == pytest.approx(0.1)


def test_mape_ignores_zero_plan_points_and_returns_zero_on_empty_plan():
    plan = np.array([0.0, 0.0, 0.0, 0.0])
    assert mape(plan, np.array([5.0, 5.0, 5.0, 5.0])) == 0.0
    plan = np.array([0.0, 0.0, 10.0, 10.0])
    assert mape(plan, np.array([0.0, 0.0, 12.0, 8.0])) == pytest.approx(0.2)


def test_wape_weighs_by_volume_unlike_mape():
    """WAPE устойчив там, где план близок к нулю: ошибка делится на общий объём, а не поточечно."""
    plan = np.array([1.0, 100.0])
    fact = np.array([2.0, 100.0])
    assert wape(plan, fact) == pytest.approx(1 / 101)
    assert wape(plan, plan.copy()) == 0.0
    assert wape(np.zeros(3), np.ones(3)) == 0.0


def test_final_deviation_is_relative_and_unsigned():
    plan = np.array([10.0, 20.0, 50.0])
    assert final_deviation(plan, np.array([10.0, 20.0, 45.0])) == pytest.approx(0.1)
    assert final_deviation(plan, np.array([10.0, 20.0, 55.0])) == pytest.approx(0.1)
    assert final_deviation(np.array([1.0, 0.0]), np.array([1.0, 5.0])) == 0.0


def test_unsmoothness_grows_with_uneven_pacing():
    plan_cum = np.cumsum(np.full(24, 10.0))
    even = plan_cum.copy()
    assert unsmoothness(plan_cum, even) == 0.0
    burst = np.cumsum(np.array([240.0] + [0.0] * 23))  # весь дневной бюджет в первый час
    assert unsmoothness(plan_cum, burst) > 1.0
    mild = np.cumsum(np.full(24, 10.0) + np.array([2.0, -2.0] * 12))
    assert 0 < unsmoothness(plan_cum, mild) < unsmoothness(plan_cum, burst)


def test_coefficient_of_variation_measures_spread_of_control_signal():
    assert coefficient_of_variation([5.0, 5.0, 5.0]) == 0.0
    assert coefficient_of_variation([]) == 0.0
    assert coefficient_of_variation([0.0, 0.0]) == 0.0
    assert coefficient_of_variation([1.0, 3.0]) == pytest.approx(0.5)
