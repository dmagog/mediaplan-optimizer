"""Планировщик: демо 1, демо 2, диагностика, фиксация канала, устойчивость к сетке."""

import time

import numpy as np

from brain.assumptions import fatigue_delta
from brain.planner import plan
from brain.planner.allocator import allocate
from brain.planner.planner import PlanningContext
from contracts import BindingConstraint, Brief, TargetKpi


def test_demo1_plan_is_complete_and_fast(catalog, curves, demo_brief):
    started = time.perf_counter()
    p = plan(demo_brief, catalog, curves)
    assert time.perf_counter() - started < 2.0
    assert p.is_feasible
    assert abs(p.total_budget_rub - 1_200_000) < 1_200_000 * 0.01
    assert len(p.allocations) == 8 and len(p.trajectory) == 21 * 24 and len(p.hourly_caps) == 21 * 24
    assert p.total_kpi > 0 and p.forecast is not None and p.forecast.p10 < p.forecast.p50 < p.forecast.p90
    for a in p.allocations:
        assert a.ctr > 0 and a.cvr > 0 and a.cpm_rub > 0
        assert a.cpa_rub is None or a.cpa_rub > 0
        assert 0 <= a.capacity_utilization <= 1
    assert abs(sum(sum(h.values()) for h in p.hourly_caps) - p.total_budget_rub) < 1.0
    assert p.explanation, "план должен объяснять порядок наливания"


def test_marginal_costs_are_equalised_across_active_channels(demo_plan):
    """Равенство предельных отдач: у каналов, не упёршихся в потолок, «цена следующей тысячи» близка."""
    active = [a for a in demo_plan.allocations if a.capacity_utilization < 0.8 and a.marginal_cost_per_1000_kpi_rub]
    costs = np.array([a.marginal_cost_per_1000_kpi_rub for a in active])
    assert len(active) >= 4
    assert costs.max() / costs.min() < 1.6, costs


def test_trajectory_is_cumulative_with_corridor(demo_plan):
    spend = [t.cum_spend_rub for t in demo_plan.trajectory]
    kpi = [t.cum_conversions for t in demo_plan.trajectory]
    assert all(b >= a for a, b in zip(spend, spend[1:], strict=False))
    assert all(b >= a for a, b in zip(kpi, kpi[1:], strict=False))
    last = demo_plan.trajectory[-1]
    assert last.band_low_spend_rub < last.cum_spend_rub < last.band_high_spend_rub
    assert 0 < demo_plan.corridor_rel < 0.5


def test_demo2_type_b_finds_sufficient_budget(catalog, curves):
    brief = Brief(target_kpi=TargetKpi.CLICKS, target_value=50_000, horizon_days=14, channel_ids=catalog.channel_ids)
    p = plan(brief, catalog, curves)
    assert p.is_feasible
    assert p.total_kpi >= 50_000 * 0.99
    assert p.forecast is not None and p.forecast.probability_of_target is not None


def test_type_b_diagnoses_binding_constraint(catalog, curves):
    """Узкий пресет: отказ с диагнозом и тремя ходами, каждый с посчитанным результатом."""
    brief = Brief(
        target_kpi=TargetKpi.CLICKS,
        target_value=50_000,
        horizon_days=14,
        channel_ids=["social_2", "social_3", "marketplace_1", "sms"],
    )
    p = plan(brief, catalog, curves)
    assert not p.is_feasible
    diag = p.infeasibility
    assert diag.binding_constraint in set(BindingConstraint)
    assert 0 < diag.max_achievable < 50_000
    kinds = {s.changed_field for s in diag.suggestions}
    assert {"horizon_days", "target_value", "channel_ids"} <= kinds
    for s in diag.suggestions:
        assert s.expected_budget_rub > 0 and s.expected_kpi > 0
    # цель, ниже потолка: считается
    ok = plan(brief.model_copy(update={"target_value": diag.max_achievable * 0.9}), catalog, curves)
    assert ok.is_feasible


def test_locked_channel_is_respected(catalog, curves, demo_brief):
    locked = demo_brief.model_copy(update={"locked": {"sms": 50_000.0}})
    p = plan(locked, catalog, curves)
    sms = next(a for a in p.allocations if a.channel_id == "sms")
    assert abs(sms.budget_rub - 50_000) < 1.0 and sms.locked
    assert abs(p.total_budget_rub - 1_200_000) < 1_200_000 * 0.01


def test_max_cpa_freezes_expensive_channels(catalog, curves, demo_brief):
    capped = demo_brief.model_copy(update={"max_cpa_rub": 500.0})
    p = plan(capped, catalog, curves)
    # Лимит задан на среднюю цену конверсии по плану в целом: отдельный канал может быть дороже,
    # если план в среднем укладывается; остановка происходит на порции, которая подняла бы среднюю
    assert p.total_budget_rub / p.total_kpi <= 500.0
    assert any("средняя цена" in step for step in p.explanation)


def test_allocation_stable_under_finer_steps(catalog, curves, demo_brief):
    """Число порций и сетка модели это численное разрешение, не параметр результата."""
    ctx = PlanningContext(catalog, curves, list(catalog.channel_ids), 21)
    pools = {cid: catalog.by_id(cid).capacity_mid for cid in catalog.channel_ids}
    from brain.assumptions import campaign_audience_multiplier
    from brain.planner.allocator import build_models

    pools = {cid: v * campaign_audience_multiplier() for cid, v in pools.items()}
    coarse = allocate(build_models(ctx.curves, 21, pools, fatigue_delta(), grid_size=160), 1_200_000, "conversions", steps=1000)
    fine = allocate(build_models(ctx.curves, 21, pools, fatigue_delta(), grid_size=320), 1_200_000, "conversions", steps=2000)
    for cid in coarse.budgets:
        assert abs(coarse.budgets[cid] - fine.budgets[cid]) <= 0.02 * 1_200_000, cid


def test_max_cpa_is_an_average_cap(catalog, curves, demo_brief):
    """Лимит задан на среднюю цену конверсии по плану: наливаем, пока средняя не упёрлась в лимит,
    поэтому при лимите 500 ₽ размещается заметно больше, чем при остановке по предельной цене."""
    capped = demo_brief.model_copy(update={"max_cpa_rub": 500.0})
    p = plan(capped, catalog, curves)
    assert p.total_budget_rub / p.total_kpi <= 500.0 * 1.01
    assert p.total_budget_rub >= 0.6 * demo_brief.budget_rub


def test_kpi_does_not_fall_with_huge_budget(catalog, curves, demo_brief):
    """Бюджет много больше ёмкости: размещается вся ёмкость, а не пустой план."""
    small = plan(demo_brief.model_copy(update={"budget_rub": 5_000_000.0}), catalog, curves)
    huge = plan(demo_brief.model_copy(update={"budget_rub": 1_000_000_000.0}), catalog, curves)
    assert huge.total_kpi >= small.total_kpi * 0.99
    assert huge.total_budget_rub >= small.total_budget_rub * 0.99


def test_brief_rejects_duplicates_and_overlock(demo_brief):
    import pytest

    with pytest.raises(ValueError):
        demo_brief.model_copy(update={"channel_ids": ["sms", "sms"]}).model_validate(
            demo_brief.model_copy(update={"channel_ids": ["sms", "sms"]}).model_dump()
        )
    with pytest.raises(ValueError):
        Brief.model_validate({**demo_brief.model_dump(), "locked": {"sms": 900_000.0, "social_1": 500_000.0}})


def test_calendar_follows_daily_demand(demo_plan, curves):
    """Бюджет дня пропорционален спросу дня: в тихий день лишние деньги некуда девать."""
    by_day: dict[int, float] = {}
    for cell in demo_plan.calendar:
        by_day[cell.day] = by_day.get(cell.day, 0.0) + cell.budget_rub
    assert abs(sum(by_day.values()) - demo_plan.total_budget_rub) < 1.0
    assert max(by_day.values()) > min(by_day.values()) * 1.02, "дни вышли одинаковыми, спрос не учтён"

    # sms — рассылка по расписанию: спрос по дням у неё ровный, и календарь у неё ровный
    def spread(cid: str) -> float:
        w = [sum(curves[cid].hourly_share(d * 24 + h) for h in range(24)) for d in range(7)]
        return max(w) - min(w)

    cid = max((a.channel_id for a in demo_plan.allocations if a.budget_rub > 0), key=spread)
    assert spread(cid) > 0.01, "ни у одного канала в ретро нет разницы дней"
    cells = {c.day: c.budget_rub for c in demo_plan.calendar if c.channel_id == cid}
    weights = [sum(curves[cid].hourly_share(d * 24 + h) for h in range(24)) for d in range(7)]
    quiet, loud = min(range(7), key=weights.__getitem__), max(range(7), key=weights.__getitem__)
    assert cells[loud + 1] > cells[quiet + 1]


def test_hourly_caps_match_the_calendar(demo_plan):
    """Часовые лимиты и календарь — одна и та же раскладка, а не два независимых счёта."""
    by_day: dict[int, float] = {}
    for cell in demo_plan.calendar:
        by_day[cell.day] = by_day.get(cell.day, 0.0) + cell.budget_rub
    for day, budget in by_day.items():
        from_caps = sum(sum(c.values()) for c in demo_plan.hourly_caps[(day - 1) * 24 : day * 24])
        assert abs(from_caps - budget) < 1.0, f"день {day}"


def test_price_ceiling_is_named_as_the_binding_constraint(catalog, curves):
    """Потолок цены связывает — значит и предлагать надо поднять его, а не менять срок."""
    brief = Brief(
        target_kpi=TargetKpi.CONVERSIONS, target_value=2_000.0, horizon_days=21,
        channel_ids=catalog.channel_ids, max_cpa_rub=300.0,
    )
    p = plan(brief, catalog, curves)
    assert p.infeasibility is not None
    assert p.infeasibility.binding_constraint is BindingConstraint.ECONOMICS
    move = next(s for s in p.infeasibility.suggestions if s.changed_field == "max_cpa_rub")
    assert move.suggested_value > 300.0

    raised = brief.model_copy(update={"max_cpa_rub": float(move.suggested_value)})
    p2 = plan(raised, catalog, curves)
    assert p2.is_feasible and p2.total_kpi >= 2_000.0 * 0.99


def test_type_a_says_why_the_budget_is_not_placed(catalog, curves):
    """Тип A не отказывает, но обязан объяснить недоразмещение: потолок цены или ёмкость."""
    capped = Brief(budget_rub=1_200_000, horizon_days=21, channel_ids=catalog.channel_ids, max_cpa_rub=100.0)
    p = plan(capped, catalog, curves)
    assert p.is_feasible and p.total_budget_rub < 1_200_000
    assert any("потолок средней цены" in line for line in p.explanation)

    huge = Brief(budget_rub=9_000_000, horizon_days=21, channel_ids=catalog.channel_ids)
    p2 = plan(huge, catalog, curves)
    assert p2.is_feasible and p2.total_budget_rub < 9_000_000
    assert any("ёмкость каналов" in line for line in p2.explanation)
    assert any("разместился бы за" in line for line in p2.explanation)
