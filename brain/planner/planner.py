"""Планировщик: бриф → медиаплан или диагноз недостижимости.

Тип A: жадное распределение заданного бюджета. Тип B: бисекция по бюджету
поверх того же алгоритма (KPI(B) монотонна и вогнута), как режимы
``fixed_budget`` и ``target_*`` у Meridian и ``max_response`` /
``target_efficiency`` у Robyn. Отказ называет связывающее ограничение и
приносит три готовых альтернативы с посчитанным результатом (сценарий С2).
"""

import hashlib
import json
import math
from dataclasses import dataclass

import numpy as np

from brain.assumptions import campaign_audience_multiplier, fatigue_delta, video_vtr
from brain.config import (
    ALLOCATION_STEPS,
    BUDGET_BISECTION_ITERATIONS,
    CAP_BISECTION_ITERATIONS,
    CORRIDOR_SIGMA_DIVISOR,
    MAX_HORIZON_DAYS,
    REACHABLE_TARGET_MARGIN,
    TYPE_B_BISECTION_STEPS,
)
from brain.curves import ResponseCurve
from brain.ml import MLBundle, ReachModel
from brain.planner.allocator import (
    AllocationResult,
    ChannelModel,
    allocate,
    build_models,
    day_weights,
)
from brain.texts import days_word, days_word_gen, kpi_label, kpi_unit, kpi_word, num, price, rub
from contracts import (
    BindingConstraint,
    Brief,
    BriefSuggestion,
    CalendarCell,
    ChannelAllocation,
    Forecast,
    Infeasibility,
    MediaPlan,
    PublicCatalog,
    TrajectoryPoint,
)


@dataclass
class PlanningContext:
    catalog: PublicCatalog
    curves: dict[str, ResponseCurve]
    channel_ids: list[str]
    days: int
    reach_model: ReachModel | None = None
    ml_model_id: str | None = None

    def models(self, days: int | None = None) -> dict[str, ChannelModel]:
        d = days or self.days
        pools = {
            cid: self.catalog.by_id(cid).capacity_mid * campaign_audience_multiplier()
            for cid in self.channel_ids
        }
        return build_models(
            {cid: self.curves[cid] for cid in self.channel_ids}, d, pools, fatigue_delta()
        )


def plan(brief: Brief, catalog: PublicCatalog, curves: dict[str, ResponseCurve], ml_bundle: MLBundle | None = None) -> MediaPlan:
    if brief.targeting != catalog.targeting:
        raise ValueError("таргетинг брифа и каталога должен совпадать; соберите ретро-историю выбранного сегмента")
    if brief.ml.enabled:
        if ml_bundle is None or ml_bundle.catalog_id != catalog.catalog_id:
            raise ValueError("для ML нужен обученный артефакт выбранного сегмента")
        if brief.ml.response_curves:
            curves = ml_bundle.curves
    missing = [cid for cid in brief.channel_ids if cid not in curves]
    if missing:
        raise ValueError(f"нет кривых для каналов {missing}")
    reach_model = ml_bundle.reach if brief.ml.reach_correction and ml_bundle else None
    model_id = ml_bundle.model_id if brief.ml.enabled and ml_bundle else None
    ctx = PlanningContext(catalog, curves, list(brief.channel_ids), brief.horizon_days, reach_model, model_id)
    models = ctx.models()
    kpi = brief.kpi_name

    if brief.is_budget_constrained:
        assert brief.budget_rub is not None
        result = allocate(models, brief.budget_rub, kpi, brief.locked, brief.max_cpa_rub, reach_model=reach_model)
        return _assemble(brief, catalog, ctx, models, result, kpi)

    assert brief.target_value is not None
    diagnosis = _diagnose(brief, catalog, ctx, models, kpi)
    if diagnosis is not None:
        return MediaPlan(
            plan_id=_plan_id(brief, catalog) + (f"-{model_id[:8]}" if model_id else ""),
            ml_model_id=model_id,
            catalog_id=catalog.catalog_id,
            brief=brief,
            kpi_name=kpi,
            infeasibility=diagnosis,
        )
    budget = _min_budget_for(models, brief.target_value, kpi, brief.locked, brief.max_cpa_rub, reach_model)
    result = allocate(models, budget, kpi, brief.locked, brief.max_cpa_rub, reach_model=reach_model)
    return _assemble(brief, catalog, ctx, models, result, kpi)


# ----------------------------------------------------------------- тип B


def _kpi_of(models: dict[str, ChannelModel], budgets: dict[str, float], kpi: str, reach_model=None) -> float:
    """Итог плана по KPI: при цели «охват» и включённой ML-модели — с вычетом пересечений."""
    if kpi == "reach" and reach_model is not None:
        return reach_model.predict({cid: models[cid].value(b, kpi) for cid, b in budgets.items()})
    return sum(models[cid].value(b, kpi) for cid, b in budgets.items())


def _total_kpi(
    models: dict[str, ChannelModel], budget: float, kpi: str, locked, max_cpa, reach_model=None,
    steps: int = TYPE_B_BISECTION_STEPS,
) -> float:
    """Сколько KPI даёт бюджет. ``steps`` — размер порции наливания.

    Поиск (бисекция по бюджету, перебор сроков) идёт грубой сеткой ради скорости, а
    числа, которые попадают человеку в глаза или в бриф, считаются той же сеткой, что
    и сам план: правило остановки по средней цене зависит от размера порции, и грубая
    порция при узком потолке рвала наливание до старта — максимум выходил нулевым.
    """
    result = allocate(models, budget, kpi, locked, max_cpa, steps=steps, reach_model=reach_model)
    return _kpi_of(models, result.budgets, kpi, reach_model)


def _max_budget(models: dict[str, ChannelModel]) -> float:
    return sum(m.max_budget for m in models.values())


def _min_budget_for(models, target: float, kpi: str, locked, max_cpa, reach_model=None) -> float:
    lo, hi = 0.0, _max_budget(models)
    for _ in range(BUDGET_BISECTION_ITERATIONS):
        mid = (lo + hi) / 2
        if _total_kpi(models, mid, kpi, locked, max_cpa, reach_model) >= target:
            hi = mid
        else:
            lo = mid
    return hi


def _budget_for(models, target: float, kpi: str, locked, max_cpa, reach_model=None) -> float:
    """Сколько денег план под цель действительно разместит.

    Бисекция даёт бюджет, который надо *дать*, а при связывающем потолке цены
    наливание останавливается раньше и тратит заметно меньше: карточка хода обещала
    3,5 млн там, где план потратит 23 тысячи. Человеку показываем второе.
    """
    budget = _min_budget_for(models, target, kpi, locked, max_cpa, reach_model)
    result = allocate(
        models, budget, kpi, locked, max_cpa, steps=ALLOCATION_STEPS, reach_model=reach_model
    )
    return sum(result.budgets.values())


def _cap_that_reaches(models, brief: Brief, target: float, kpi: str, reach_model) -> float | None:
    """Наименьший потолок средней цены, при котором цель снова достижима.

    Нижняя граница — цена плана под цель без потолка; верхняя — вся ёмкость,
    поделённая на цель: если цель достижима в пределах ёмкости, средняя цена такого
    плана заведомо ниже этого числа, поэтому отрезок гарантированно содержит ответ.
    Бисекция идёт грубой сеткой, а найденное значение проверяется рабочей: правило
    остановки по средней цене зависит от размера порции, и на фиксациях грубая
    проверка обещала потолок, при котором план потом недобирал цель в разы.
    """
    def reaches(cap: float, steps: int) -> bool:
        return _total_kpi(models, _max_budget(models), kpi, brief.locked, cap, reach_model, steps=steps) >= target

    lo = _min_budget_for(models, target, kpi, brief.locked, None, reach_model) / target
    hi = _max_budget(models) / target
    if not reaches(hi, ALLOCATION_STEPS):
        return None
    lo = min(lo, hi)
    for _ in range(CAP_BISECTION_ITERATIONS):
        mid = (lo + hi) / 2
        if reaches(mid, TYPE_B_BISECTION_STEPS):
            hi = mid
        else:
            lo = mid
    # рабочая сетка строже грубой, поэтому от найденной границы поднимаемся, пока цель не пройдёт
    cap, ceiling = _kopecks_up(hi), _kopecks_up(_max_budget(models) / target)
    for _ in range(CAP_BISECTION_ITERATIONS):
        if reaches(cap, ALLOCATION_STEPS):
            return cap
        if cap >= ceiling:
            return None
        cap = _kopecks_up(min((cap + ceiling) / 2, ceiling))
    return None


def _kopecks_up(value: float) -> float:
    """Округление цены вверх до копейки: округление вниз давало потолок, при котором цель не проходит."""
    return math.ceil(value * 100) / 100


def _lower_target_suggestion(models, brief: Brief, max_kpi: float, kpi: str, reach_model) -> BriefSuggestion | None:
    """Ход «снизить цель»: 95 % достижимого максимума, чтобы цель не висела на потолке.

    Если достижимого максимума нет (узкий потолок цены не пропускает ни один канал),
    хода нет: бриф с нулевой целью не проходит валидацию, а карточка «снизить цель до
    0» выходила первой и рекомендованной.
    """
    reachable = max_kpi * REACHABLE_TARGET_MARGIN
    if round(reachable) < 1:
        return None
    budget = _budget_for(models, reachable, kpi, brief.locked, brief.max_cpa_rub, reach_model)
    return BriefSuggestion(
        description=f"Снизить цель до {num(reachable)} {kpi_label(kpi)} при тех же каналах и сроке",
        changed_field="target_value",
        suggested_value=float(round(reachable)),
        expected_kpi=float(reachable),
        expected_budget_rub=float(budget),
    )


def _diagnose(brief: Brief, catalog: PublicCatalog, ctx: PlanningContext, models, kpi: str) -> Infeasibility | None:
    target = brief.target_value
    assert target is not None
    # максимум считаем рабочей сеткой: это число человек видит на экране и от него
    # берётся ход «снизить цель», а грубая порция при узком потолке давала ноль
    max_kpi = _total_kpi(
        models, _max_budget(models), kpi, brief.locked, brief.max_cpa_rub, ctx.reach_model, steps=ALLOCATION_STEPS
    )
    if max_kpi >= target:
        return None

    suggestions: list[BriefSuggestion] = []
    # 0. потолок средней цены: если без него цель достижима, связывает именно он.
    # Ёмкость и срок тут ни при чём, и предлагать надо поднять потолок, а не двигать бриф.
    if brief.max_cpa_rub is not None:
        free_max = _total_kpi(
            models, _max_budget(models), kpi, brief.locked, None, ctx.reach_model, steps=ALLOCATION_STEPS
        )
        if free_max >= target:
            # без потолка цель достижима — значит связывает он, и другого ответа тут быть не может:
            # прежняя версия при неудачном подборе проваливалась в ветки срока и ёмкости, где
            # максимум посчитан С потолком, и текст обещал то, чего нет
            cap = _cap_that_reaches(models, brief, target, kpi, ctx.reach_model)
            if cap is not None and cap <= brief.max_cpa_rub:
                cap = _kopecks_up(brief.max_cpa_rub + 0.01)
            if cap is not None:
                suggestions.append(
                    BriefSuggestion(
                        description=f"Поднять потолок средней цены до {price(cap)} за {kpi_unit(kpi)}",
                        changed_field="max_cpa_rub",
                        suggested_value=float(cap),
                        expected_kpi=float(target),
                        expected_budget_rub=float(
                            _budget_for(models, target, kpi, brief.locked, cap, ctx.reach_model)
                        ),
                    )
                )
            else:
                suggestions.append(
                    BriefSuggestion(
                        description="Убрать потолок средней цены",
                        changed_field="max_cpa_rub",
                        suggested_value=None,
                        expected_kpi=float(target),
                        expected_budget_rub=float(
                            _budget_for(models, target, kpi, brief.locked, None, ctx.reach_model)
                        ),
                    )
                )
            lower = _lower_target_suggestion(models, brief, max_kpi, kpi, ctx.reach_model)
            if lower is not None:
                suggestions.append(lower)
            reachable_at = f" — при потолке от {price(cap)}" if cap is not None else " только без потолка"
            return Infeasibility(
                binding_constraint=BindingConstraint.ECONOMICS,
                explanation=(
                    f"Ёмкости каналов хватает, но потолок средней цены {price(brief.max_cpa_rub)} "
                    f"пропускает не больше {num(max_kpi)} {kpi_label(kpi)}; без потолка достижимо "
                    f"{num(free_max)}, цель {num(target)} достижима{reachable_at}."
                ),
                max_achievable=max_kpi,
                suggestions=suggestions,
            )
    # 1. увеличить срок: минимальный горизонт, при котором цель достижима.
    # Ищем двоичным поиском, а не перебором день за днём: максимум достижимого растёт
    # с горизонтом (ёмкость каналов складывается по дням), поэтому достаточно проверить
    # логарифм от диапазона. Перебор до 90 дней стоил 7–32 секунды: каждый шаг заново
    # собирает модели каналов, и дорожает это с длиной горизонта.
    cache: dict[int, dict[str, ChannelModel]] = {}

    def models_for(days: int) -> dict[str, ChannelModel]:
        if days not in cache:
            cache[days] = ctx.models(days)
        return cache[days]

    def reaches(days: int) -> bool:
        m = models_for(days)
        return _total_kpi(m, _max_budget(m), kpi, brief.locked, brief.max_cpa_rub, ctx.reach_model) >= target

    min_days = None
    low, high = brief.horizon_days + 1, MAX_HORIZON_DAYS
    if low <= high and reaches(high):
        while low < high:
            mid = (low + high) // 2
            if reaches(mid):
                high = mid
            else:
                low = mid + 1
        min_days = low
        m = models_for(min_days)
        budget = _budget_for(m, target, kpi, brief.locked, brief.max_cpa_rub, ctx.reach_model)
        suggestions.append(
            BriefSuggestion(
                description=f"Увеличить срок до {min_days} {days_word_gen(min_days)}",
                changed_field="horizon_days",
                suggested_value=min_days,
                expected_kpi=float(target),
                expected_budget_rub=float(budget),
            )
        )
    # 2. снизить цель до достижимого максимума с запасом 5 %
    lower = _lower_target_suggestion(models, brief, max_kpi, kpi, ctx.reach_model)
    if lower is not None:
        suggestions.append(lower)
    # 3. добавить каналы, которых нет в пресете
    extra = [cid for cid in catalog.channel_ids if cid not in brief.channel_ids and cid in ctx.curves]
    if extra:
        wide = PlanningContext(catalog, ctx.curves, list(brief.channel_ids) + extra, brief.horizon_days, ctx.reach_model, ctx.ml_model_id)
        wide_models = wide.models()
        wide_max = _total_kpi(wide_models, _max_budget(wide_models), kpi, brief.locked, brief.max_cpa_rub, ctx.reach_model)
        if wide_max >= target:
            budget = _budget_for(wide_models, target, kpi, brief.locked, brief.max_cpa_rub, ctx.reach_model)
            suggestions.append(
                BriefSuggestion(
                    description=f"Добавить каналы: {', '.join(extra)}",
                    changed_field="channel_ids",
                    suggested_value=list(brief.channel_ids) + extra,
                    expected_kpi=float(target),
                    expected_budget_rub=float(budget),
                )
            )
            constraint = BindingConstraint.CHANNEL_SET
            explanation = (
                f"При выбранных каналах максимум за {brief.horizon_days} {days_word(brief.horizon_days)} "
                f"{num(max_kpi)} {kpi_label(kpi)}; с добавлением {', '.join(extra)} цель достижима."
            )
            return Infeasibility(
                binding_constraint=constraint, explanation=explanation, max_achievable=max_kpi, suggestions=suggestions
            )

    if min_days is not None:
        constraint = BindingConstraint.HORIZON
        explanation = (
            f"Ёмкости каналов хватает, но не за {brief.horizon_days} {days_word(brief.horizon_days)}: потолок "
            f"{num(max_kpi)} {kpi_label(kpi)}; цель достижима минимум за {min_days} {days_word(min_days)}."
        )
    else:
        constraint = BindingConstraint.CAPACITY
        explanation = (
            f"Суммарная ёмкость выбранных каналов даёт не более {num(max_kpi)} {kpi_label(kpi)} "
            f"даже при максимальном выкупе; цель {num(target)} недостижима."
        )
    return Infeasibility(
        binding_constraint=constraint, explanation=explanation, max_achievable=max_kpi, suggestions=suggestions
    )


def _capacity_ceiling(ctx: PlanningContext, days: int) -> float:
    """Сколько денег каналы способны выкупить за ``days`` дней.

    Потолок канала за кампанию — дневной потолок, умноженный на сумму весов дней,
    поэтому число считается прямо по профилю, без сборки сеток моделей: сборка
    стоит четверть секунды на каждый горизонт, а перебору сроков их нужно семь.
    """
    return sum(
        ctx.curves[cid].max_daily_spend * float(day_weights(ctx.curves[cid].hourly_profile, days).sum())
        for cid in ctx.channel_ids
    )


def _shortfall_notes(brief: Brief, ctx: PlanningContext, models, result: AllocationResult, kpi: str) -> list[str]:
    """Почему бюджет размещён не весь: потолок цены, ёмкость каналов или и то, и другое.

    Тип A не проходит через диагноз недостижимости — бриф с бюджетом всегда даёт
    план, — поэтому раньше человек видел только строку «упёрлись в потолок или в
    лимит цены» и сам гадал, во что именно. Считаем это тем же способом, что и
    диагноз типа B: перекладываем без потолка цены и смотрим на разницу.
    """
    assert brief.budget_rub is not None
    placed = sum(result.budgets.values())
    notes = [f"размещено {rub(placed)} из {rub(brief.budget_rub)}"]
    # последняя порция каждого канала осесть не успевает, поэтому «весь бюджет» — с запасом
    slack = brief.budget_rub / ALLOCATION_STEPS * len(ctx.channel_ids)

    capacity_binds = brief.budget_rub > _max_budget(models) + slack
    if brief.max_cpa_rub is not None:
        # то же наливание и та же сетка, что у самого плана: на грубой сетке разница
        # порций перекрывала вклад потолка, и связывающий потолок пропадал из текста
        free = allocate(
            models, brief.budget_rub, kpi, brief.locked, None,
            steps=ALLOCATION_STEPS, reach_model=ctx.reach_model,
        )
        free_placed = sum(free.budgets.values())
        if free_placed > placed + 1.0:
            free_kpi = _kpi_of(models, free.budgets, kpi, ctx.reach_model)
            notes.append(
                f"потолок средней цены {price(brief.max_cpa_rub)} за {kpi_unit(kpi)} не пропустил "
                f"{rub(free_placed - placed)}: без него разместилось бы {rub(free_placed)} "
                f"и прогноз вырос бы до {num(free_kpi)} {kpi_word(free_kpi, kpi)}"
            )
            if placed <= 0:
                notes.append("ни один канал не проходит потолок: поднимите его или снимите")
            if not capacity_binds:
                return notes

    ceiling = _max_budget(models)
    notes.append(
        f"ёмкость каналов за {brief.horizon_days} {days_word(brief.horizon_days)} принимает не больше "
        f"{rub(ceiling)}: деньги сверх этого мир просто не выкупит"
    )
    low, high = brief.horizon_days + 1, MAX_HORIZON_DAYS
    if low <= high and _capacity_ceiling(ctx, high) >= brief.budget_rub + slack:
        while low < high:
            mid = (low + high) // 2
            if _capacity_ceiling(ctx, mid) >= brief.budget_rub + slack:
                high = mid
            else:
                low = mid + 1
        notes.append(f"ёмкости хватило бы за {low} {days_word(low)} или на более широком наборе каналов")
    else:
        notes.append("даже за максимальный срок брифа этот набор каналов столько не выкупит: нужны ещё каналы")
    return notes


# --------------------------------------------------------------- сборка


def _assemble(brief: Brief, catalog: PublicCatalog, ctx: PlanningContext, models, result: AllocationResult, kpi: str) -> MediaPlan:
    days = brief.horizon_days
    hours = days * 24
    allocations: list[ChannelAllocation] = []
    calendar: list[CalendarCell] = []
    hourly_caps: list[dict[str, float]] = [{} for _ in range(hours)]
    per_channel_cum_spend = {cid: np.zeros(hours) for cid in ctx.channel_ids}
    per_channel_cum_reach = {cid: np.zeros(hours) for cid in ctx.channel_ids}
    cum = {k: np.zeros(hours) for k in ("spend", "impressions", "clicks", "conversions", "reach")}

    budget_total = sum(result.budgets.values())
    corridor_rel = 0.0
    for cid in ctx.channel_ids:
        model = models[cid]
        channel = catalog.by_id(cid)
        b = result.budgets[cid]
        out = model.outcome(b)
        # бюджет по дням — пропорционально спросу дня, а не поровну: в тихий день
        # столько же денег некуда девать, в громкий их не хватает
        daily_budget = model.daily_budgets(b)
        hourly_spend = np.array(
            [
                daily_budget[h // 24] * ctx.curves[cid].hourly_share(h) / max(model.day_weights[h // 24], 1e-9)
                for h in range(hours)
            ]
        )  # доля часа внутри своих суток: вес дня уже учтён в daily_budget
        cum_spend = np.cumsum(hourly_spend)
        per_channel_cum_spend[cid] = cum_spend
        cum["spend"] += cum_spend
        # KPI по часам: дневное накопление из модели (усталость по дням),
        # внутри суток пропорционально доле расхода часа. Пропорция «KPI ∝ расход»
        # на всю кампанию завышала бы хвост: свежая аудитория отвечает лучше.
        for key in ("impressions", "clicks", "conversions", "reach"):
            daily_cum = out.daily_cum[key]
            for day in range(days):
                prev = daily_cum[day - 1] if day > 0 else 0.0
                day_total = daily_cum[day] - prev
                block = hourly_spend[day * 24 : (day + 1) * 24]
                within = np.cumsum(block) / block.sum() if block.sum() > 0 else np.linspace(1 / 24, 1, 24)
                cum[key][day * 24 : (day + 1) * 24] += prev + day_total * within
                if key == "reach":
                    per_channel_cum_reach[cid][day * 24 : (day + 1) * 24] = prev + day_total * within
        for h in range(hours):
            hourly_caps[h][cid] = float(hourly_spend[h])
        for day in range(1, days + 1):
            calendar.append(CalendarCell(day=day, channel_id=cid, budget_rub=float(daily_budget[day - 1])))

        spend_eff = out.spend if out.spend > 0 else b
        marginal = result.marginal_cost_per_kpi.get(cid)
        daily_spend = out.daily_cum["spend"]
        daily_clicks = out.daily_cum["clicks"]
        clicks_per_rub = []
        for d in range(days):
            s_prev = daily_spend[d - 1] if d > 0 else 0.0
            c_prev = daily_clicks[d - 1] if d > 0 else 0.0
            ds, dc = daily_spend[d] - s_prev, daily_clicks[d] - c_prev
            clicks_per_rub.append(float(dc / ds) if ds > 0 else 0.0)
        allocations.append(
            ChannelAllocation(
                channel_id=cid,
                display_name=channel.display_name,
                budget_rub=float(b),
                impressions=float(out.impressions),
                unique_reach=float(out.reach),
                clicks=float(out.clicks),
                conversions=float(out.conversions),
                ctr=float(out.clicks / out.impressions) if out.impressions else 0.0,
                cvr=float(out.conversions / out.clicks) if out.clicks else 0.0,
                vtr=video_vtr() if channel.supports_video else None,
                cpm_rub=float(spend_eff / out.impressions * 1000) if out.impressions else 0.0,
                cpc_rub=float(spend_eff / out.clicks) if out.clicks else None,
                cpa_rub=float(spend_eff / out.conversions) if out.conversions else None,
                frequency=float(out.impressions / out.reach) if out.reach else 0.0,
                capacity_utilization=float(min(b / model.max_budget, 1.0)) if model.max_budget else 0.0,
                marginal_cost_per_1000_kpi_rub=float(marginal * 1000) if marginal else None,
                expected_daily_clicks_per_rub=clicks_per_rub,
                locked=cid in brief.locked,
            )
        )
        if budget_total > 0:
            # диапазон каталога трактуем как P10–P90; коридор ±1σ = полуширина / z(0.90)
            corridor_rel += ctx.curves[cid].uncertainty / CORRIDOR_SIGMA_DIVISOR * (b / budget_total)

    if ctx.reach_model is not None:
        cum["reach"] = np.array([ctx.reach_model.predict({cid: values[h] for cid, values in per_channel_cum_reach.items()}) for h in range(hours)])
    kpi_curve = cum[kpi]
    trajectory = [
        TrajectoryPoint(
            hour=h + 1,
            cum_spend_rub=float(cum["spend"][h]),
            cum_impressions=float(cum["impressions"][h]),
            cum_clicks=float(cum["clicks"][h]),
            cum_conversions=float(cum["conversions"][h]),
            cum_reach=float(cum["reach"][h]),
            band_low_spend_rub=float(cum["spend"][h] * (1 - corridor_rel)),
            band_high_spend_rub=float(cum["spend"][h] * (1 + corridor_rel)),
            band_low_kpi=float(kpi_curve[h] * (1 - corridor_rel)),
            band_high_kpi=float(kpi_curve[h] * (1 + corridor_rel)),
            by_channel_cum_spend_rub={cid: float(per_channel_cum_spend[cid][h]) for cid in ctx.channel_ids},
        )
        for h in range(hours)
    ]
    total_kpi = float(kpi_curve[-1])
    forecast = Forecast(
        kpi_name=kpi,
        p10=total_kpi * (1 - corridor_rel),
        p50=total_kpi,
        p90=total_kpi * (1 + corridor_rel),
        probability_of_target=(
            None if brief.target_value is None else _probability(total_kpi, corridor_rel, brief.target_value)
        ),
    )
    explanation = list(result.steps)
    explanation.append("ML: прогноз охвата скорректирован моделью, обученной на общих охватах ретро-кампаний."
                       if ctx.reach_model else "Прогноз охвата суммирует каналы и не учитывает межканальные пересечения; фактический охват кампании дедуплицируется.")
    if brief.ml.response_curves:
        explanation.append("ML: распределение использует обученные кривые показов, кликов и конверсий по бюджету.")
    if result.unspent > 0:
        if brief.is_budget_constrained:
            explanation.extend(_shortfall_notes(brief, ctx, models, result, kpi))
        else:
            explanation.append(f"не распределено {rub(result.unspent)}: каналы упёрлись в потолок ёмкости или цены")
    return MediaPlan(
        plan_id=_plan_id(brief, catalog) + (f"-{ctx.ml_model_id[:8]}" if ctx.ml_model_id else ""),
        ml_model_id=ctx.ml_model_id,
        catalog_id=catalog.catalog_id,
        brief=brief,
        kpi_name=kpi,
        allocations=allocations,
        calendar=calendar,
        trajectory=trajectory,
        hourly_caps=hourly_caps,
        forecast=forecast,
        corridor_rel=float(corridor_rel),
        explanation=explanation,
    )


def _probability(p50: float, rel: float, target: float) -> float:
    """Вероятность выполнить цель при нормальном разбросе с σ ≈ коридор / 1.28 (P10–P90)."""
    if p50 <= 0:
        return 0.0
    sigma = max(p50 * rel, 1e-9)  # rel уже равен ±1σ
    from statistics import NormalDist

    return float(1 - NormalDist(p50, sigma).cdf(target))


def _plan_id(brief: Brief, catalog: PublicCatalog) -> str:
    payload = json.dumps({"brief": brief.model_dump(mode="json"), "catalog": catalog.catalog_id}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]
