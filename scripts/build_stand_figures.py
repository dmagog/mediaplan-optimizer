"""Гистограммы отклонений по мирам из уже посчитанного стенда.

Запуск: ``python scripts/build_stand_figures.py``. Читает ``results/comparison.json``
и рисует ``docs/figures/stand_hist_<сценарий>.png`` — те же картинки, что рисует
``run_demos.py`` по ходу счёта. Отдельный вход нужен, чтобы перерисовать их без
двухчасового пересчёта стенда: matplotlib в зависимостях проекта нет, и на машине,
где его не было в момент прогона, картинки молча оставались от прошлого мира.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.config import CASE_THRESHOLD  # noqa: E402
from scripts.run_demos import _histogram  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparison", type=Path, default=ROOT / "results" / "comparison.json")
    parser.add_argument("--figures", type=Path, default=ROOT / "docs" / "figures")
    args = parser.parse_args()

    data = json.loads(args.comparison.read_text(encoding="utf-8"))
    for scenario, strategies in data.items():
        dev_a = np.array([r["final_deviation_kpi"] for r in strategies["adaptive"]["per_run"]])
        dev_s = np.array([r["final_deviation_kpi"] for r in strategies["static"]["per_run"]])
        _histogram(dev_a, dev_s, scenario, len(dev_a), args.figures, CASE_THRESHOLD)
        print(f"{scenario}: {len(dev_a)} миров, медианы {np.median(dev_a):.1%} против {np.median(dev_s):.1%}")


if __name__ == "__main__":
    main()
