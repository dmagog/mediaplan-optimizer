"""Сборка demo-notebook со сквозным сценарием: код выполняется, вывод попадает в ноутбук.

Зачем свой сборщик: ноутбук нужен рабочим и с выводами, а тянуть в зависимости
проекта jupyter ради одного файла не хочется. Формат .ipynb — обычный JSON,
выполнение — exec в общем пространстве имён с перехватом stdout. Запуск:

    .venv/bin/python scripts/build_demo_notebook.py
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "notebooks" / "demo.ipynb"
MD, CODE = "markdown", "code"

CELLS: list[tuple[str, str]] = [
    (MD, """# MediaPlan Optimizer: сквозной сценарий

Ноутбук проходит весь путь сервиса на данных, которые он генерирует сам:
внешние рекламные кабинеты не нужны.

1. Каталог каналов и кривые отклика.
2. Постановка A: задан бюджет, максимизируем конверсии.
3. Медиаплан: распределение, прогноз, метрики по каналам и по плану целиком.
4. Постановка B: задана цель, которой не достичь, — диагноз и три хода.
5. Кампания по часам с шоком рынка и решением человека.
6. Итог: обещали, получили, что дало ведение.

Пересобрать ноутбук с выводами: `.venv/bin/python scripts/build_demo_notebook.py`.
Кабинет с теми же сценариями: `uvicorn app.main:app --port 8000`."""),

    (CODE, '''import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()))

from brain.curves import build_curves
from brain.planner import plan as build_plan
from contracts import Brief, SeedBundle, TargetKpi
from harness.retro import collect_retro_history
from harness.runner import RunConfig, run_campaign
from world import build_catalog


def n(value, digits=0):
    """Разряды неразрывным пробелом, как в кабинете."""
    return f"{value:,.{digits}f}".replace(",", "\\u00a0")


catalog = build_catalog(0)          # зерно каталога фиксировано: результат воспроизводим
curves = build_curves(collect_retro_history(catalog), catalog)

print(f"каналов в каталоге: {len(catalog.channels)}")
for ch in catalog.channels:
    low, high = ch.daily_unique_capacity_band
    ecpm = f"{n(ch.expected_ecpm_range[0])}–{n(ch.expected_ecpm_range[1])}"
    print(f"  {ch.display_name:38} ёмкость {n(low):>8}–{n(high):<8} чел/сут   eCPM {ecpm:>9} ₽")'''),

    (MD, """## Постановка A: бюджет 1,2 млн ₽ на 21 день, максимум конверсий

Бриф — единственный вход планировщика. Истинных параметров рынка он не видит:
только публичный каталог и кривые, собранные по пробным кампаниям прошлых
миров."""),

    (CODE, '''brief_a = Brief(budget_rub=1_200_000, horizon_days=21, channel_ids=catalog.channel_ids)
plan_a = build_plan(brief_a, catalog, curves)

print(f"прогноз: {n(plan_a.total_kpi)} конверсий, разброс {n(plan_a.forecast.p10)}–{n(plan_a.forecast.p90)}")
print(f"цена конверсии: {n(plan_a.total_budget_rub / plan_a.total_kpi)} ₽")
print()
print(f"{'канал':38} {'бюджет, ₽':>12} {'доля':>6} {'конверсии':>10} {'CPA, ₽':>8} {'выкуп':>7}")
for a in plan_a.allocations:
    print(f"{a.display_name:38} {n(a.budget_rub):>12} {a.budget_rub / plan_a.total_budget_rub:>6.1%} "
          f"{n(a.conversions):>10} {n(a.cpa_rub or 0):>8} {a.capacity_utilization:>7.0%}")'''),

    (MD, """## Метрики по медиаплану целиком

Требование постановки: те же показатели, что по каналам, но и по плану в
целом. Объёмы складываются, качество и цены считаются от объёмов: среднее
арифметическое долей по каналам дало бы неверную картину."""),

    (CODE, '''from app.main import _plan_series, _plan_totals

t = _plan_totals(plan_a)
print(f"бюджет      {n(t['budget_rub']):>12} ₽")
print(f"показы      {n(t['impressions']):>12}")
print(f"охват       {n(t['unique_reach']):>12}    частота {t['frequency']:.1f}")
print(f"клики       {n(t['clicks']):>12}    CTR {t['ctr']:.2%}")
print(f"конверсии   {n(t['conversions']):>12}    CR  {t['cvr']:.2%}")
print(f"CPM {n(t['cpm_rub'])} ₽   CPC {n(t['cpc_rub'], 2)} ₽   CPA {n(t['cpa_rub'])} ₽")
print(f"VTR {t['vtr']:.0%} — посчитан на {t['vtr_impressions_share']:.0%} показов (видеоформаты)")

series = _plan_series(plan_a)
print()
print("накопление по неделям:")
for w in series["weeks"]:
    print(f"  неделя {w['week']} (дни {w['days'][0]}–{w['days'][1]}): "
          f"бюджет {n(w['budget_rub']):>9} ₽, накоплено {n(w['cum_kpi']):>6} конверсий")'''),

    (MD, """## Постановка B: 50 000 кликов за 14 дней на узком наборе

Сервис не отвечает пустым планом. Он называет максимум при этих условиях, во
что упирается, и даёт ходы с посчитанным бюджетом."""),

    (CODE, '''narrow = ["social_1", "social_2", "marketplace_1", "sms"]
brief_b = Brief(target_kpi=TargetKpi.CLICKS, target_value=50_000, horizon_days=14, channel_ids=narrow)
plan_b = build_plan(brief_b, catalog, curves)

d = plan_b.infeasibility
print(f"достижимо: {plan_b.is_feasible}")
print(f"максимум при этих условиях: {n(d.max_achievable)} кликов из {n(brief_b.target_value)}")
print(f"упирается в: {d.binding_constraint.value}")
print()
for i, s in enumerate(sorted(d.suggestions, key=lambda x: x.expected_budget_rub), 1):
    print(f"  вариант {i}: {s.description}")
    print(f"             {n(s.expected_kpi)} кликов при бюджете {n(s.expected_budget_rub)} ₽")'''),

    (MD, """## Применяем ход: тот же бриф на всех восьми каналах

Каждый ход — изменённый бриф, а не текст для человека: его можно применить и
пересчитать."""),

    (CODE, '''brief_b_all = Brief(target_kpi=TargetKpi.CLICKS, target_value=50_000, horizon_days=14,
                    channel_ids=catalog.channel_ids)
plan_b_all = build_plan(brief_b_all, catalog, curves)

print(f"достижимо: {plan_b_all.is_feasible}")
print(f"нужный бюджет: {n(plan_b_all.total_budget_rub)} ₽ на {n(plan_b_all.total_kpi)} кликов")
print(f"шанс выполнить цель: {plan_b_all.forecast.probability_of_target:.0%}")'''),

    (MD, """## Кампания по часам: шок рынка и решение человека

План утверждён, кампания идёт час за часом. Сценарий рынка — programmatic
перестаёт отдавать показы на трое суток. Переносы дешевле лимита полномочий
исполнитель делает сам, дороже — приносит человеку карточкой."""),

    (CODE, '''seeds = SeedBundle(catalog_seed=0, world_seed=1, noise_seed=10_001)
brief_run = Brief(budget_rub=1_200_000, horizon_days=21, channel_ids=catalog.channel_ids,
                  automation_limit_rub=50_000)
plan_run = build_plan(brief_run, catalog, curves)

# auto_apply_above_limit=False: переносы дороже лимита ждут человека, как в кабинете
run = run_campaign(plan_run, catalog, curves,
                   RunConfig("adaptive", "channel_pause", seeds, auto_apply_above_limit=False))
frozen = run_campaign(plan_run, catalog, curves, RunConfig("static", "channel_pause", seeds))

pending = [p for p in run.proposals if p.applied_by == "pending"]
print(f"часов в кампании: {len(run.hours)}")
print(f"переносов сделала автоматика: {sum(1 for p in run.proposals if p.applied_by == 'system')}")
print(f"карточек ждут человека: {len(pending)}")
print()
for p in pending[:2]:
    print(f"  час {p.hour}: перенести {n(p.amount_rub)} ₽ из «{p.from_channel}» в «{p.to_channel}»")
    print(f"    почему: {p.cause}")
    print(f"    если не делать: недоберём около {p.inaction_kpi_shortfall_pct:.0f}% результата")'''),

    (MD, """## Человек одобряет переносы

Решение перепрогоняет кампанию на тех же случайных числах: разница в итоге —
цена именно решения, а не удачного совпадения."""),

    (CODE, '''pending_hours = tuple(p.hour for p in pending)
approved = run_campaign(plan_run, catalog, curves,
                        RunConfig("adaptive", "channel_pause", seeds, auto_apply_above_limit=False,
                                  approved_hours=pending_hours))

print(f"карточки не решены: {n(run.actual_kpi)} конверсий, расход {n(run.actual_spend)} ₽")
print(f"карточки одобрены:  {n(approved.actual_kpi)} конверсий, расход {n(approved.actual_spend)} ₽")
print(f"цена решения: {approved.actual_kpi - run.actual_kpi:+.0f} конверсий")'''),

    (MD, """## Итог: обещали, получили, что дало ведение

Рядом с фактом всегда стоит контрфактный двойник — тот же план на том же
рынке и на тех же случайных числах, но без единого вмешательства."""),

    (CODE, '''print(f"обещали:            {n(plan_run.total_kpi)} конверсий за {n(plan_run.total_budget_rub)} ₽")
print(f"получили:           {n(approved.actual_kpi)} конверсий за {n(approved.actual_spend)} ₽")
print(f"план без изменений: {n(frozen.actual_kpi)} конверсий за {n(frozen.actual_spend)} ₽")
print()
print(f"отклонение по результату: наше {approved.final_deviation_kpi:.1%}, "
      f"без ведения {frozen.final_deviation_kpi:.1%}")
print(f"отклонение по расходу:    наше {approved.final_deviation_spend:.1%}, "
      f"без ведения {frozen.final_deviation_spend:.1%}")
print(f"ведение дало: {approved.actual_kpi - frozen.actual_kpi:+.0f} конверсий")'''),

    (MD, """## Тот же путь через API кабинета

Кабинет ходит в те же функции по HTTP. Здесь запросы идут в приложение
напрямую, поднимать сервер не нужно."""),

    (CODE, '''from fastapi.testclient import TestClient

from app.main import app

with TestClient(app) as client:
    plan = client.post("/api/plan", json={"mode": "A", "budget_rub": 1_200_000,
                                          "horizon_days": 21, "preset": "all"}).json()
    print(f"POST /api/plan → {n(plan['total_kpi'])} конверсий, план {plan['plan_id']}")
    client.post(f"/api/plan/{plan['plan_id']}/approve")
    run_view = client.post("/api/run", json={"plan_id": plan["plan_id"],
                                             "scenario_id": "channel_pause"}).json()
    v = run_view["verdict"]
    print(f"POST /api/run  → {n(v['actual_kpi'])} конверсий, отклонение {v['final_deviation_kpi']:.1%}")
    bad = client.post("/api/plan", json={"mode": "A", "budget_rub": -5000,
                                         "horizon_days": 21, "preset": "all"})
    print(f"неверный бриф  → {bad.status_code}: {bad.json()['detail']}")'''),

    (MD, """## Что дальше

- Кабинет: `uvicorn app.main:app --port 8000`; экраны и роли описаны в `docs/cabinet.md`.
- Стенд на 100 мирах: `python scripts/run_demos.py --seeds 100`, отчёт в `results/report.md`.
- Откуда каждое число: `config/benchmarks.yaml`, `config/assumptions.yaml`, `config/geo.yaml`."""),
]


def build() -> None:
    namespace: dict[str, object] = {"__name__": "__main__"}
    cells: list[dict] = []
    executed = 0
    for kind, source in CELLS:
        if kind == MD:
            cells.append({"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)})
            continue
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                exec(compile(source, "<notebook>", "exec"), namespace)  # noqa: S102 — код самого ноутбука
        except Exception as exc:  # ячейка должна падать заметно, а не молча попасть в артефакт
            print(f"ячейка упала: {exc}", file=sys.stderr)
            raise
        executed += 1
        text = buffer.getvalue()
        cells.append({
            "cell_type": "code",
            "execution_count": executed,
            "metadata": {},
            "outputs": ([{"output_type": "stream", "name": "stdout", "text": text.splitlines(keepends=True)}]
                        if text else []),
            "source": source.splitlines(keepends=True),
        })
    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": sys.version.split()[0]},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"собран {OUT.relative_to(ROOT)}: ячеек {len(cells)}, из них с кодом {executed}, "
          f"{OUT.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    build()
