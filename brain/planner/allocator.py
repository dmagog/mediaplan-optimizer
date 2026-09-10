"""Распределение бюджета по предельной отдаче.

Для вогнутых сепарабельных кривых отклика оптимум описывается равенством
предельных отдач по каналам (Little & Lodish, «A Media Planning Calculus»,
1969; та же логика в аллокаторах Meridian и Robyn). Реализовано жадным
наливанием порциями: ёмкость входит естественно (у насыщенного канала
предельная отдача ноль), счёт занимает миллисекунды, а порядок наливания
сам является объяснением плана для медиапланера.
"""

from dataclasses import dataclass, field

import numpy as np

from brain.config import ALLOCATION_STEPS as STEPS
from brain.config import MODEL_GRID_SIZE as GRID_SIZE
from brain.curves import ResponseCurve

OUTCOME_KEYS = ("impressions", "reach", "clicks", "conversions", "spend")


@dataclass
class Outcome:
    """Результат канала за кампанию и его накопление по дням."""

    impressions: float
    reach: float
    clicks: float
    conversions: float
    spend: float
    daily_cum: dict[str, np.ndarray] = field(default_factory=dict)


def day_weights(profile: np.ndarray, days: int, day_offset: int = 0) -> np.ndarray:
    """Вес спроса каждого дня кампании: сумма долей его часов, средние сутки — единица.

    Профиль по часам недели приходит из ретро-наблюдений (brain/curves.py), поэтому
    разница будней и выходных планировщику видна, а скрытых параметров мира он не знает.
    Отдельная функция, а не метод: потолок ёмкости за N дней считается по ней напрямую,
    без сборки сеток моделей.
    """
    weights = np.array(
        [profile[((day_offset + d) * 24 + h) % len(profile)] for d in range(days) for h in range(24)],
        dtype=float,
    ).reshape(days, 24).sum(axis=1)
    return np.where(weights > 0, weights, 1.0)


@dataclass
class ChannelModel:
    """Функция «бюджет на кампанию → результат» одного канала с усталостью по дням.

    Кривая описывает день при частоте 1; накопление частоты и падение CTR
    делает модель по дням, ровно как мир делает по часам. Применять итоговую
    частоту ко всему объёму нельзя: в первый день частота равна единице.
    """

    channel_id: str
    curve: ResponseCurve
    days: int
    pool: float
    fatigue_delta: float
    grid_size: int = GRID_SIZE
    day_offset: int = 0  # с какого дня недели идёт кампания: важно при перепланировании остатка
    grid_budget: np.ndarray = field(default_factory=lambda: np.zeros(1))
    grid: dict[str, np.ndarray] = field(default_factory=dict)
    grid_step: float = 1.0
    day_weights: np.ndarray = field(default_factory=lambda: np.ones(1))

    def __post_init__(self) -> None:
        self.day_weights = self._weights()
        # ёмкость дня растёт вместе с его спросом, поэтому потолок кампании — по сумме весов
        max_total = self.curve.max_daily_spend * float(self.day_weights.sum())
        self.grid_budget = np.linspace(0.0, max(max_total, 1.0), self.grid_size)
        self.grid_step = float(self.grid_budget[1] - self.grid_budget[0])
        rows = [self.simulate(b) for b in self.grid_budget]
        self.grid = {key: np.array([getattr(r, key) for r in rows]) for key in OUTCOME_KEYS}

    def _weights(self) -> np.ndarray:
        return day_weights(self.curve.hourly_profile, self.days, self.day_offset)

    def daily_budgets(self, total_budget: float) -> np.ndarray:
        """Бюджет по дням: пропорционально спросу дня, а не поровну.

        При равном спросе это и есть равномерная раскладка. Конверсий такая раскладка
        не прибавляет: выкуп однороден (вдвое больше инвентаря — вдвое больше показов
        на ту же ставку), поэтому для вогнутой кривой равный расход на единицу спроса
        и есть оптимум, а не приближение к нему. Смысл в том, что календарь плана и
        часовые лимиты совпадают с тем, как кампания тратит на самом деле.
        """
        w = self.day_weights
        return total_budget * w / float(w.sum())

    def simulate(self, total_budget: float) -> Outcome:
        w = self.day_weights
        # день веса w покупает столько же на рубль, сколько средние сутки: и спрос,
        # и доступный инвентарь дня масштабируются одним и тем же весом
        per_average_day = total_budget / float(w.sum())
        imps_base = self.curve.impressions_at(per_average_day)
        spend_base = self.curve.effective_spend(per_average_day)
        base_ctr, base_cvr = self.curve.rates_at(per_average_day)
        cum = {key: np.zeros(self.days) for key in OUTCOME_KEYS}
        cum_imps = cum_reach = clicks = conv = spend = 0.0
        for d in range(self.days):
            imps_day = w[d] * imps_base
            spend_day = w[d] * spend_base
            new_reach = (self.pool - cum_reach) * (1 - np.exp(-imps_day / self.pool)) if self.pool > 0 else 0.0
            cum_reach += new_reach
            cum_imps += imps_day
            spend += spend_day
            freq = cum_imps / cum_reach if cum_reach > 0 else 1.0
            ctr = base_ctr / (1 + self.fatigue_delta * max(freq - 1, 0.0))
            day_clicks = imps_day * ctr
            clicks += day_clicks
            conv += day_clicks * base_cvr
            cum["impressions"][d], cum["reach"][d], cum["clicks"][d] = cum_imps, cum_reach, clicks
            cum["conversions"][d], cum["spend"][d] = conv, spend
        return Outcome(cum_imps, cum_reach, clicks, conv, spend, cum)

    def value(self, total_budget: float, kpi: str) -> float:
        """Значение по сетке «бюджет → результат». Сетка равномерная, поэтому берём
        соседей по индексу: np.interp на скаляре стоил половину времени диагноза
        (шестьсот тысяч вызовов на один отказ), а результат тот же.
        """
        grid = self.grid[kpi]
        if total_budget <= 0.0:
            return float(grid[0])
        x = total_budget / self.grid_step
        i = int(x)
        if i >= grid.size - 1:
            return float(grid[-1])
        return float(grid[i] + (x - i) * (grid[i + 1] - grid[i]))

    def outcome(self, total_budget: float) -> Outcome:
        """Точный пересчёт по дням для итоговой строки плана и траектории."""
        return self.simulate(min(max(total_budget, 0.0), self.max_budget))

    @property
    def max_budget(self) -> float:
        return float(self.grid_budget[-1])


def build_models(
    curves: dict[str, ResponseCurve],
    days: int,
    pools: dict[str, float],
    fatigue_delta: float,
    grid_size: int = GRID_SIZE,
    day_offset: int = 0,
) -> dict[str, ChannelModel]:
    return {
        cid: ChannelModel(
            channel_id=cid, curve=curve, days=days, pool=pools[cid], fatigue_delta=fatigue_delta,
            grid_size=grid_size, day_offset=day_offset,
        )
        for cid, curve in curves.items()
    }


@dataclass
class AllocationResult:
    budgets: dict[str, float]
    unspent: float
    steps: list[str]
    frozen: dict[str, str]  # канал → причина заморозки
    marginal_cost_per_kpi: dict[str, float | None]


def allocate(
    models: dict[str, ChannelModel],
    budget: float,
    kpi: str,
    locked: dict[str, float] | None = None,
    max_cost_per_kpi: float | None = None,
    steps: int = STEPS,
    reach_model=None,
    prior_reach: dict[str, float] | None = None,
) -> AllocationResult:
    """Жадное наливание порциями ``budget / steps`` в канал с максимальным приростом KPI.

    ``locked`` фиксирует бюджеты каналов (человек двигает канал руками,
    остальные перераспределяются). ``max_cost_per_kpi`` это лимит на среднюю
    цену единицы KPI: порции наливаются по возрастанию предельной цены, и
    наливание останавливается там, где средняя перевалила бы за лимит (жадный
    алгоритм при вогнутых кривых даёт максимум KPI при ограничении на среднюю
    цену). Лимит считается по свободной части плана, без фиксаций: фиксацию
    поставил человек, и если он зафиксировал канал дороже лимита, средняя по
    плану выйдет выше — но остальные деньги всё равно тратятся не дороже лимита,
    и план об этом прямо говорит. Считать среднюю по всему плану не годится ни в
    одну сторону: с дорогой фиксацией наливание рвалось на первой же порции, а
    если разрешить порции, которые среднюю снижают, лимит перестаёт ограничивать
    что-либо — свободные каналы покупались в три-четыре раза дороже лимита, лишь
    бы дешевле фиксации.
    ``reach_model`` включает совместный прирост охвата с вычетом пересечений.
    """
    locked = dict(locked or {})
    budgets = {cid: 0.0 for cid in models}
    for cid, value in locked.items():
        budgets[cid] = min(value, models[cid].max_budget)
    free_budget = budget - sum(budgets.values())
    # порция от размещаемой суммы, а не от бюджета брифа: при бюджете много больше ёмкости
    # порция переставала влезать в потолок любого канала и план выходил пустым
    eps = max(min(budget, sum(m.max_budget for m in models.values())) / steps, 1.0)
    frozen: dict[str, str] = {cid: "зафиксирован вручную" for cid in locked}
    order: list[str] = []
    explanation: list[str] = []
    joint_reach = reach_model if kpi == "reach" else None
    reach_values = {cid: model.value(budgets[cid], "reach") for cid, model in models.items()} if joint_reach else {}
    spent = sum(budgets.values())
    # накопленный KPI нужен для лимита на среднюю цену; при совместном охвате он тоже совместный
    total_kpi = (
        joint_reach.incremental(reach_values, prior_reach)
        if joint_reach
        else sum(models[cid].value(b, kpi) for cid, b in budgets.items())
    )
    # что стоит на фиксациях: лимит средней цены считается поверх этого
    locked_spend, locked_kpi = spent, total_kpi

    while free_budget >= eps * 0.5:
        # последняя порция не больше остатка: иначе план стоит дороже бюджета брифа
        # на половину порции, а при фиксации, не кратной порции, — заметно дороже
        step = min(eps, free_budget)
        best_cid, best_gain = None, 0.0
        current_reach = joint_reach.incremental(reach_values, prior_reach) if joint_reach else 0
        for cid, model in models.items():
            if cid in frozen:
                continue
            b = budgets[cid]
            if b + step > model.max_budget:
                frozen[cid] = "ёмкость исчерпана"
                continue
            gain = model.value(b + step, kpi) - model.value(b, kpi)
            if joint_reach:
                # при цели «охват» прирост считается совместно: ML-модель вычитает пересечение
                # аудиторий каналов, поэтому вклад порции зависит от того, что уже куплено
                candidate = {**reach_values, cid: model.value(b + step, "reach")}
                gain = joint_reach.incremental(candidate, prior_reach) - current_reach
            if gain > best_gain:
                best_cid, best_gain = cid, gain
        if best_cid is None:
            break
        if max_cost_per_kpi is not None:
            free_average = (spent - locked_spend + step) / max(total_kpi - locked_kpi + best_gain, 1e-9)
            if free_average > max_cost_per_kpi:
                for cid in models:
                    frozen.setdefault(cid, f"средняя цена за единицу KPI достигла лимита {max_cost_per_kpi:,.0f} ₽")
                break
        spent += step
        total_kpi += best_gain
        if best_cid not in order:
            order.append(best_cid)
            explanation.append(
                f"шаг {len(order)}: {best_cid} получает бюджет, первая порция по "
                f"{step / best_gain:,.0f} ₽ за единицу KPI (цена последней порции в таблице плана)"
            )
        budgets[best_cid] += step
        if joint_reach:
            reach_values[best_cid] = models[best_cid].value(budgets[best_cid], "reach")
        free_budget -= step

    for cid, reason in frozen.items():
        if cid not in locked:
            explanation.append(f"{cid}: остановлен, {reason}")
    if max_cost_per_kpi is not None and locked_kpi > 0 and locked_spend / locked_kpi > max_cost_per_kpi:
        explanation.append(
            f"фиксации стоят {locked_spend / locked_kpi:,.0f} ₽ за единицу KPI, дороже лимита "
            f"{max_cost_per_kpi:,.0f} ₽: средняя цена плана выше лимита из-за них, остальной бюджет — нет"
        )

    marginal: dict[str, float | None] = {}
    for cid, model in models.items():
        b = budgets[cid]
        gain = model.value(min(b + eps, model.max_budget), kpi) - model.value(b, kpi)
        if joint_reach:
            candidate = {**reach_values, cid: model.value(min(b + eps, model.max_budget), "reach")}
            gain = joint_reach.incremental(candidate, prior_reach) - joint_reach.incremental(reach_values, prior_reach)
        marginal[cid] = eps / gain if gain > 1e-9 else None
    return AllocationResult(
        budgets=budgets,
        unspent=free_budget if free_budget > eps * 0.5 else 0.0,
        steps=explanation,
        frozen=frozen,
        marginal_cost_per_kpi=marginal,
    )
