"""Сборка итогового отчёта: report/report.html из документов репозитория.

Отчёт по требованию задания: постановка, модель и решатель, архитектура,
эксперименты, выводы. Текст не дублируется руками — разделы берутся из README,
docs/report.md и results/report.md, картинки вшиваются в файл, поэтому HTML
открывается где угодно без папки с ресурсами. Запуск:

    .venv/bin/python scripts/build_report.py
"""

from __future__ import annotations

import base64
import datetime as dt
import re
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "report" / "report.html"

CSS = """
:root{--paper:#f3f2f2;--ink:#201e1d;--accent:#ec3013;--line:#d6d2d0;--muted:#6f6a67}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
     font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
main{max-width:900px;margin:0 auto;padding:56px 32px 96px}
h1{font-size:40px;line-height:1.1;letter-spacing:-.02em;margin:0 0 8px}
h2{font-size:26px;line-height:1.2;margin:56px 0 12px;padding-top:20px;border-top:2px solid var(--ink)}
h3{font-size:19px;margin:32px 0 8px}
p,li{max-width:74ch}
a{color:var(--ink)}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;background:#e7e4e2;padding:1px 4px}
pre{background:#e7e4e2;padding:14px 16px;overflow:auto}
pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;margin:16px 0;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:7px 10px;text-align:right;vertical-align:top}
th:first-child,td:first-child{text-align:left}
th{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:400;
   border-bottom:2px solid var(--line)}
img{max-width:100%;display:block;margin:16px 0;border:1px solid var(--line)}
blockquote{margin:16px 0;padding:8px 16px;border-left:3px solid var(--accent);color:var(--muted)}
.lead{font-size:19px;color:var(--muted);max-width:70ch}
.meta{margin:24px 0 0;color:var(--muted);font-size:14px}
.toc{margin:40px 0 0;padding:20px 24px;border:2px solid var(--ink)}
.toc ol{margin:8px 0 0;padding-left:20px} .toc li{margin:2px 0}
@media print{body{background:#fff}main{padding:0}h2{page-break-after:avoid}table,img{page-break-inside:avoid}}
"""


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def section(text: str, title: str, level: int = 2) -> str:
    """Раздел документа по заголовку, без самого заголовка."""
    mark = "#" * level
    # в документах перед короткими словами стоит неразрывный пробел, поэтому ищем
    # заголовок по словам, а не по точной строке
    words = r"[\s\u00a0]+".join(re.escape(w) for w in title.split())
    pattern = rf"^{mark} {words}\s*$"
    match = re.search(pattern, text, re.M)
    if not match:
        raise SystemExit(f"в исходнике нет раздела «{title}»")
    rest = text[match.end():]
    end = re.search(rf"^#{{1,{level}}} ", rest, re.M)
    return rest[: end.start()].strip() if end else rest.strip()


def inline_images(md: str) -> str:
    """Картинки вшиваем в файл: отчёт должен открываться одним файлом."""
    def repl(m: re.Match[str]) -> str:
        alt, src = m.group(1), m.group(2)
        path = ROOT / src
        if not path.exists():
            return m.group(0)
        mime = {".png": "image/png", ".gif": "image/gif", ".svg": "image/svg+xml"}.get(path.suffix, "image/png")
        data = base64.b64encode(path.read_bytes()).decode()
        return f"![{alt}](data:{mime};base64,{data})"
    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", repl, md)


def compose() -> str:
    readme, report, results = read("README.md"), read("docs/report.md"), read("results/report.md")
    today = dt.date.today().strftime("%d.%m.%Y")
    parts = [
        f"""# MediaPlan Optimizer

<p class="lead">Система оптимизации медиабюджетов и автоматического
медиапланирования: собирает медиаплан по восьми каналам, ведёт кампанию час за
часом и перекладывает бюджет, когда рынок расходится с планом.</p>

<p class="meta">Итоговый отчёт · {today} · Родион Оркин, Тимофей Лисоченко,
Георгий Мамарин · <a href="https://github.com/dmagog/mediaplan-optimizer">github.com/dmagog/mediaplan-optimizer</a></p>

<div class="toc"><b>Содержание</b><ol>
<li>Постановка задачи</li><li>Что делает сервис</li><li>Модель рынка и решатель</li>
<li>Архитектура</li><li>Эксперименты</li><li>Границы и упрощения</li><li>Выводы</li>
</ol></div>

## 1. Постановка задачи

""" + section(readme, "Зачем"),

        "## 2. Что делает сервис\n\n" + section(readme, "Что делает сервис")
        + "\n\n### Шесть сценариев\n\n" + section(readme, "Шесть сценариев, ради которых всё сделано"),

        "## 3. Модель рынка и решатель\n\n"
        + section(report, "1. Откуда планировщик знает рынок")
        + "\n\n### Правило распределения (тип A)\n\n" + section(report, "2. Правило распределения (тип A)")
        + "\n\n### Достаточный бюджет и отказ (тип B)\n\n" + section(report, "3. Достаточный бюджет и отказ (тип B)")
        + "\n\n### Перераспределение по ходу кампании\n\n" + section(report, "4. Правило перераспределения (исполнение)"),

        "## 4. Архитектура\n\n" + section(readme, "Архитектура: пять пакетов"),

        "## 5. Эксперименты\n\n### Экраны сервиса\n\n" + section(readme, "Экраны кабинета")
        + "\n\n### Демонстрации A и B\n\n"
        + section(results, "Демо 1. Бюджет 1,2 млн ₽, 21 день, максимум конверсий")
        + "\n\n" + section(results, "Демо 2. 50 000 кликов за 14 дней")
        + "\n\n### Стенд на ста мирах\n\n" + section(readme, "Результаты стенда"),

        "## 6. Границы и упрощения\n\n" + section(readme, "Границы и упрощения"),

        """## 7. Выводы

Задача медиаплана решается не одним расчётом, а двумя связанными: распределить
бюджет до запуска и удержать план по ходу кампании. Расчёт занимает 0,1 секунды
против рабочего дня в Excel, но главный выигрыш даёт второй контур: на ста
парных мирах ведение приносит результат ближе к обещанному в 84 % случаев, а
доля миров, где и расход, и результат уложились в порог кейса, растёт с 44 до
60 %.

Три решения оказались важнее остальных. Первое: рядом с каждым числом стоит
вторая величина — разброс вместо точки, цена решения и цена бездействия рядом с
предложением. Второе: сервис никогда не отказывает молча, а называет максимум и
ходы с посчитанным бюджетом. Третье: человек остаётся в контуре — переносы
дороже лимита ждут его решения, и цена этого решения видна по факту, потому что
кампания перепрогоняется на тех же случайных числах.

Что осталось за границей прототипа: оптимизация раскладки по дням и неделям
(сейчас бюджет канала делится по дням равномерно), региональные цены,
аукцион второй цены и обучение на логах ставок. Все упрощения перечислены в
разделе 6 и в журнале решений репозитория.""",
    ]
    return "\n\n".join(parts)


def build() -> None:
    md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    body = md.render(inline_images(compose()))
    html = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MediaPlan Optimizer — итоговый отчёт</title>
<style>{CSS}</style></head>
<body><main>{body}</main></body></html>
"""
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"собран {OUT.relative_to(ROOT)}: {OUT.stat().st_size // 1024} КБ, "
          f"картинок вшито {html.count('data:image')}")


if __name__ == "__main__":
    build()
