"""Таблицы стенда в README и описании проекта — из артефактов, а не из головы.

Запуск: ``python scripts/sync_doc_numbers.py`` (``--check`` только сверяет и падает,
если расхождение есть — годится для CI). Читает ``results/comparison.json`` и
``results/modes_100.json`` и перезаписывает числовые ячейки двух таблиц: пять
сценариев стенда и четыре режима резерва. Переносить их руками трижды подряд
оказалось надёжным способом ошибиться на процент.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
NB = " "  # неразрывный пробел перед знаком процента, как во всех текстах
THRESHOLD = 0.20  # порог кейса

# подпись строки в таблице → сценарий в артефакте
SCENARIOS = {
    "без шока": "stable",
    "CTR −40 % в крупном канале": "ctr_drop",
    "CPM ×2": "cpm_spike",
    "пауза канала": "channel_pause",
    "SMS-база −50 %": "capacity_cut",
}
MODES = {
    "заморозка (план буквально)": "static",
    "держать KPI на плане": "hold",
    "держать план, баланс (по умолчанию)": "balanced",
    "выжимать максимум": "max",
}


def _pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}".replace(".", ",") + NB + "%"


def _norm(text: str) -> str:
    """Подпись строки без неразрывных пробелов: в документах они стоят перед знаком процента."""
    return text.replace(NB, " ").strip()


def _scenario_rows(comparison: dict) -> dict[str, str]:
    rows = {}
    for label, key in SCENARIOS.items():
        s = comparison[key]  # служебные ключи вида __stand__ здесь не запрашиваются
        dev = {name: np.array([r["final_deviation_kpi"] for r in s[name]["per_run"]]) for name in ("static", "adaptive")}
        cells = []
        for name in ("static", "adaptive"):
            cells.append(
                f"{_pct(s[name]['mean']['final_deviation_spend'])} / "
                f"{_pct(float(np.median(dev[name])))} / "
                f"{_pct(float(np.mean(dev[name] <= THRESHOLD)), 0)}"
            )
        wins = _pct(s["adaptive"]["win_rate_vs_static"]["final_deviation_kpi"], 0)
        rows[_norm(label)] = f" {cells[0]} | {cells[1]} | {wins} |"
    return rows


def _mode_rows(modes: dict) -> dict[str, str]:
    rows = {}
    for label, key in MODES.items():
        d = modes[key]
        kpi = f"{d['kpi_mean']:,.0f}".replace(",", NB)
        rows[_norm(label)] = (
            f" {_pct(d['spend_mean'])} | {_pct(d['kpi_median'])} | {_pct(d['both_in'], 0)} | {kpi} |"
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="только сверить, ничего не писать")
    args = parser.parse_args()

    comparison = json.loads((ROOT / "results" / "comparison.json").read_text(encoding="utf-8"))
    modes = json.loads((ROOT / "results" / "modes_100.json").read_text(encoding="utf-8"))["stable"]
    wanted = {**_scenario_rows(comparison), **_mode_rows(modes)}

    stale: list[str] = []
    for name in ("README.md", "docs/PROJECT.md"):
        path = ROOT / name
        lines = path.read_text(encoding="utf-8").split("\n")
        changed = False
        for i, line in enumerate(lines):
            if not line.startswith("| "):
                continue
            # подпись строки берём из файла как есть: в ней могут стоять неразрывные пробелы
            label = line.split("|")[1]
            cells = wanted.get(_norm(label))
            if cells is None:
                continue
            row = f"|{label}|{cells}"
            if line != row:
                stale.append(f"{name}: {line.strip()}\n{' ' * len(name)}  → {row.strip()}")
                lines[i] = row
                changed = True
        if changed and not args.check:
            path.write_text("\n".join(lines), encoding="utf-8")

    if args.check:
        if stale:
            print("Таблицы разошлись с артефактами:\n" + "\n".join(stale))
            sys.exit(1)
        print("таблицы сходятся с артефактами")
    else:
        print("\n".join(stale) if stale else "менять нечего")


if __name__ == "__main__":
    main()
