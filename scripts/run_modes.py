"""Режимы резерва на одних и тех же мирах: чем платит каждый выбор.

Запуск: ``python scripts/run_modes.py --seeds 100``. Пишет в ``results/``:
``modes_100.json`` — четыре режима на сценарии без шока, ``maxmode_100.json`` —
режим «выжимать максимум» на двух сценариях. Таблицы отсюда стоят в README и в
``docs/business_position.md``: раньше их считал разовый скрипт, и повторить
цифры после правок мира было нечем.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.curves import build_curves  # noqa: E402
from brain.planner import plan  # noqa: E402
from contracts import Brief  # noqa: E402
from harness.compare import compare_strategies  # noqa: E402
from harness.retro import collect_retro_history  # noqa: E402
from world import build_catalog  # noqa: E402

RESULTS = Path(__file__).resolve().parent.parent / "results"
THRESHOLD = 0.20  # «в пороге» — отклонение не больше 20 %, порог кейса

# режим → (держать план, баланс резерва); static считается тем же вызовом
MODES = {
    "hold": (True, 0.0),  # держать KPI ровно на плане
    "balanced": (True, 1.0),  # недорасход равен перевыполнению — по умолчанию
    "max": (False, 1.0),  # выжимать максимум KPI
}


def _stats(runs, base=None) -> dict[str, float]:
    spend = np.array([r.final_deviation_spend for r in runs])
    kpi = np.array([r.final_deviation_kpi for r in runs])
    out = {
        "spend_mean": float(spend.mean()),
        "spend_median": float(np.median(spend)),
        "spend_p90": float(np.quantile(spend, 0.9)),
        "kpi_median": float(np.median(kpi)),
        "kpi_p90": float(np.quantile(kpi, 0.9)),
        "kpi_in": float(np.mean(kpi <= THRESHOLD)),
        "both_in": float(np.mean((kpi <= THRESHOLD) & (spend <= THRESHOLD))),
        "kpi_mean": round(float(np.mean([r.actual_kpi for r in runs])), 2),
    }
    if base is not None:
        out["win"] = float(np.mean(kpi < np.array([r.final_deviation_kpi for r in base])))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--scenarios", default="stable,ctr_drop")
    args = parser.parse_args()

    catalog = build_catalog(0)
    curves = build_curves(collect_retro_history(catalog), catalog)
    demo = Brief(budget_rub=1_200_000, horizon_days=21, channel_ids=catalog.channel_ids)
    p = plan(demo, catalog, curves)

    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    modes: dict[str, dict[str, dict[str, float]]] = {}
    maxmode: dict[str, dict[str, float]] = {}
    for scenario in scenarios:
        row: dict[str, dict[str, float]] = {}
        base_runs = None
        for name, (hold, balance) in MODES.items():
            strategies = ("static", "adaptive") if base_runs is None else ("adaptive",)
            stats = compare_strategies(
                p, catalog, curves, strategies=strategies, scenario_id=scenario,
                seeds=args.seeds, hold_plan=hold, reserve_balance=balance,
            )
            if base_runs is None:
                base_runs = stats["static"].runs
                row["static"] = _stats(base_runs)
            row[name] = _stats(stats["adaptive"].runs, base_runs)
            row[name]["plan_kpi"] = float(p.total_kpi)
            print(f"{scenario:>10s} {name:>9s}: недорасход {row[name]['spend_mean']:.1%}, "
                  f"KPI {row[name]['kpi_median']:.1%}, оба в пороге {row[name]['both_in']:.0%}", flush=True)
        modes[scenario] = row
        maxmode[scenario] = {
            **{k: v for k, v in row["max"].items() if k != "spend_mean"},
            "static_kpi_median": row["static"]["kpi_median"],
            "static_kpi_in": row["static"]["kpi_in"],
        }

    RESULTS.mkdir(exist_ok=True)
    # число миров в имени файла: README и docs/business_position.md цитируют его как
    # «100 миров», и прогон на 20 мирах не должен молча подменять эти таблицы
    first = {scenarios[0]: modes[scenarios[0]], "seeds": args.seeds}
    (RESULTS / f"modes_{args.seeds}.json").write_text(json.dumps(first, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    maxmode["seeds"] = args.seeds
    (RESULTS / f"maxmode_{args.seeds}.json").write_text(json.dumps(maxmode, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"готово: results/modes_{args.seeds}.json, results/maxmode_{args.seeds}.json")


if __name__ == "__main__":
    main()
