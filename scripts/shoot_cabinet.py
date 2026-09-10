"""Снимки кабинета для README и слайдов: пятнадцать экранов и гифка плеера.

Запуск (нужен Playwright и Pillow, в зависимости проекта они не входят):

    uvicorn app.main:app --port 8002        # кабинет, который снимаем
    python scripts/shoot_cabinet.py         # --base, --out и --only меняют адрес, папку и набор

Раньше съёмка жила разовым скриптом вне репозитория, и снимки в README отставали
от интерфейса на несколько правок: на них были и прежние подписи, и прежние числа.
Теперь пересъёмка — одна команда, и её видно в списке скриптов.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ALL_CHANNELS = "META.presets.all.channels"
DEMO1 = f"setMode('A'); setChannels({ALL_CHANNELS}); setRegions([]); setTargeting(); FORM.locked = {{}}; " \
        "setMoney('budget', 1200000); document.querySelector('#horizon').value = 21; " \
        "document.querySelector('#objective').value = 'max_conversions'; setMoney('max_cpa', ''); " \
        "setMoney('auto_limit', 50000); syncCpa();"


def _plan(pg, script: str, wait: int = 2500) -> None:
    """Собрать бриф и дождаться экрана плана (или отказа)."""
    pg.evaluate(f"() => {{ showScreen('brief'); {script} makePlan(); }}")
    pg.wait_for_selector("#plan-h1", timeout=120000)
    pg.wait_for_timeout(wait)


def _run_campaign(pg) -> None:
    """Утвердить план и досчитать прогон до первого часа."""
    pg.click("#btn-approve")
    # два пути: менеджер жмёт «Перейти к кампании» и «Запустить», у заказчика утверждение
    # ведёт к прогону сразу. Обе кнопки есть в разметке и скрыты по очереди, поэтому
    # опрашиваем состояние, пока не появится плеер
    for _ in range(180):
        if pg.query_selector("#p-play"):
            break
        if pg.is_visible("#btn-gorun"):
            pg.click("#btn-gorun")
        elif pg.is_visible("#btn-run") and "заново" not in (pg.inner_text("#btn-run") or ""):
            pg.click("#btn-run")
        pg.wait_for_timeout(500)
    pg.wait_for_selector("#p-play", timeout=180000)
    pg.wait_for_timeout(1500)


def _to_the_end(pg) -> None:
    """Доиграть кампанию до конца, решая карточки: плеер намеренно встаёт на каждой.

    Переносы дороже лимита полномочий ждут решения человека, поэтому одного нажатия
    «К концу кампании» не хватает — иначе съёмка висла на первой же карточке.
    """
    for _ in range(80):
        if pg.query_selector("#btn-gosummary"):
            return
        # карточки перерисовываются после каждого решения, поэтому найденный элемент
        # может отвалиться от DOM между проверкой и нажатием — это не ошибка, а повтор
        for selector in ("button[data-d='approve']", "#p-end"):
            try:
                el = pg.query_selector(selector)
                if el and el.is_enabled():
                    el.click()
                    pg.wait_for_timeout(2000)
                    break
            except Exception:
                pg.wait_for_timeout(500)
                break
        else:
            pg.wait_for_timeout(1000)  # пересчёт после решения: кнопки заблокированы
    raise RuntimeError("кампания не доиграна до конца: карточки не кончаются")


def _shot(pg, out: Path, name: str, selector: str | None = None) -> None:
    target = pg.query_selector(selector) if selector else None
    (target or pg).screenshot(path=str(out / f"{name}.png"))
    print(f"  {name}.png")


def _screens(pg, out: Path, prefix: str) -> None:
    """Четыре экрана одного кабинета: бриф, план, кампания на дне шока, итог."""
    _plan(pg, DEMO1)
    pg.evaluate("() => showScreen('brief')")
    pg.wait_for_timeout(600)
    _shot(pg, out, f"{prefix}-01-brief")
    pg.evaluate("() => showScreen('plan')")
    pg.wait_for_timeout(600)
    _shot(pg, out, f"{prefix}-02-plan")
    _run_campaign(pg)
    pg.evaluate("() => showHour(11 * 24)")
    pg.wait_for_timeout(1200)
    _shot(pg, out, f"{prefix}-03-run-day11")
    _to_the_end(pg)
    pg.wait_for_timeout(1200)
    pg.click("#btn-gosummary")
    pg.wait_for_timeout(1500)
    _shot(pg, out, f"{prefix}-04-summary")


def _manager_extras(pg, out: Path) -> None:
    """Экран отказа, правила, география, бегунки, правка в строке, бриф с сегментом."""
    _plan(pg, "setMode('B'); setChannels(META.presets.narrow.channels); "
              "document.querySelector('#target_kpi').value = 'clicks'; setMoney('target_value', 50000); "
              "document.querySelector('#horizon').value = 14; setMoney('max_cpa', ''); syncCpa();", wait=4000)
    _shot(pg, out, "manager-02-refusal")

    pg.evaluate("() => { showScreen('brief'); document.querySelector('#btn-rules').click(); }")
    pg.wait_for_timeout(900)
    _shot(pg, out, "rules-modal", "#rules-modal")
    pg.evaluate("() => document.querySelector('#btn-rules-close').click()")
    pg.wait_for_timeout(400)

    pg.evaluate(f"() => {{ showScreen('brief'); {DEMO1} setRegions(['cfo', 'szfo', 'pfo']); "
                "setTargeting({age_groups: ['25_34', '35_44'], genders: ['female']}); }")
    pg.wait_for_timeout(1200)
    _shot(pg, out, "brief-audience")

    _plan(pg, f"{DEMO1} setRegions(['cfo', 'szfo', 'pfo']); setTargeting({{age_groups: ['25_34', '35_44']}});")
    _shot(pg, out, "plan-geo", "#plan-geo")
    _shot(pg, out, "plan-sliders", "#manual-block")

    _plan(pg, DEMO1)
    pg.evaluate("""() => {
        const cell = document.querySelector('#plan-table td.edit, #plan-table .cell-edit, #plan-table input');
        if (cell && cell.focus) cell.focus();
    }""")
    pg.wait_for_timeout(600)
    _shot(pg, out, "plan-table-edit", "#plan-table")


def _customer_extras(pg, out: Path) -> None:
    """Итог заказчику без шапки: плитки и короткая таблица."""
    _shot(pg, out, "summary-short", "#summary-body")


def _player_gif(pg, out: Path, frames: int = 8, width: int = 980) -> None:
    """Гифка плеера: идём по событиям, а не по равным отрезкам времени.

    Подпись в README обещает остановки на карточке решения и на шоке — значит и кадры
    надо брать там, где плеер сам встаёт. Кнопка «До следующего события» делает ровно
    это, поэтому гифка показывает то, что обещано, а не просто бегущие часы.
    """
    from PIL import Image

    _plan(pg, DEMO1)
    _run_campaign(pg)
    shots: list[Path] = []

    def frame() -> None:
        path = out / f"_frame{len(shots):02d}.png"
        pg.screenshot(path=str(path))
        shots.append(path)

    frame()
    for _ in range(frames - 1):
        if pg.query_selector("#btn-gosummary"):
            break
        moved = False
        for selector in ("#p-next", "button[data-d='approve']"):
            try:
                el = pg.query_selector(selector)
                if el and el.is_enabled():
                    el.click()
                    pg.wait_for_timeout(2200)
                    moved = True
                    break
            except Exception:
                pg.wait_for_timeout(600)
                moved = True
                break
        if not moved:
            pg.wait_for_timeout(800)
            continue
        frame()

    images = []
    for path in shots:
        img = Image.open(path)
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
        images.append(img.convert("P", palette=Image.ADAPTIVE, colors=64))
    images[0].save(
        out / "player.gif", save_all=True, append_images=images[1:], duration=1400, loop=0, optimize=True
    )
    for path in shots:
        path.unlink()
    size = (out / "player.gif").stat().st_size / 1024
    print(f"  player.gif — {len(images)} кадров, {size:.0f} КБ")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8002")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "figures" / "cabinet")
    parser.add_argument("--only", default="", help="через запятую: manager, customer, extras, gif")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    only = {p.strip() for p in args.only.split(",") if p.strip()} or {"manager", "customer", "extras", "gif"}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for role, parts in (("manager", {"manager", "extras", "gif"}), ("customer", {"customer"})):
            if not (only & parts):
                continue
            page = browser.new_page(viewport={"width": 1360, "height": 940})
            page.on("pageerror", lambda e: print(f"  ОШИБКА СТРАНИЦЫ: {e}"))
            page.goto(f"{args.base}/?role={role}")
            page.wait_for_selector("#btn-demo1", timeout=60000)
            print(f"кабинет {role}:")
            if role in only:
                _screens(page, args.out, role)
                if role == "customer":
                    _customer_extras(page, args.out)
            if role == "manager" and "extras" in only:
                _manager_extras(page, args.out)
            if role == "manager" and "gif" in only:
                _player_gif(page, args.out)
            page.close()
        browser.close()
    print(f"снимки в {args.out}")


if __name__ == "__main__":
    main()
