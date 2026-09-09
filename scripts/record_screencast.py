"""Запись скринкаста кабинета: постановка A → план → кампания → итог, затем постановка B → отказ.

Артефакт по требованию задания: короткое видео на 2–5 минут. Пишется браузером
через Playwright, звука нет, паузы расставлены так, чтобы экраны успевали
читаться. Нужны Playwright и его chromium (в зависимости проекта не входят):

    pip install playwright && playwright install chromium
    uvicorn app.main:app --port 8001          # кабинет, который снимаем
    python scripts/record_screencast.py        # webm рядом со скриптом
    ffmpeg -i запись.webm -c:v libx264 -crf 30 -an docs/screencast.mp4
"""
import os
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = str(Path(__file__).resolve().parent.parent / 'build' / 'video')
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT, exist_ok=True)
BASE = 'http://127.0.0.1:8001'

def scroll_to(pg, selector, offset=130, pause=900):
    """Плавно подводим блок под шапку: в записи это читается как взгляд человека."""
    pg.evaluate(
        """(args) => {
            const el = document.querySelector(args.selector);
            if (!el) return;
            const top = el.getBoundingClientRect().top + window.scrollY - args.offset;
            window.scrollTo({top, behavior: 'smooth'});
        }""",
        {"selector": selector, "offset": offset},
    )
    pg.wait_for_timeout(pause)


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={'width': 1280, 'height': 800}, record_video_dir=OUT,
                        record_video_size={'width': 1280, 'height': 800})
    pg = ctx.new_page()
    # кабинет защищает утверждённый план вопросом «пересчитать заново?»: в записи отвечаем «да»
    pg.on('dialog', lambda d: d.accept())
    # 1. Бриф: задача, бюджет, каналы, сегмент, география
    pg.goto(f'{BASE}/?role=manager')
    pg.wait_for_selector('#geo-map g')
    pg.wait_for_timeout(4000)
    pg.click('#budget')
    pg.fill('#budget', '')
    pg.type('#budget', '1200000', delay=90)
    pg.wait_for_timeout(1200)
    scroll_to(pg, '#f-target', 130, 1500)
    pg.click(".tag[data-ax=age_groups][data-k='25_34']")
    pg.wait_for_timeout(900)
    pg.click(".tag[data-ax=age_groups][data-k='35_44']")
    pg.wait_for_timeout(1400)
    pg.click(".tag[data-ax=age_groups][data-k='25_34']")
    pg.click(".tag[data-ax=age_groups][data-k='35_44']")
    pg.wait_for_timeout(800)
    pg.click('#geo-map g[data-district=cfo]')
    pg.wait_for_timeout(1300)
    pg.click('#geo-map g[data-district=szfo]')
    pg.wait_for_timeout(1800)
    pg.click('#geo-presets button')
    pg.wait_for_timeout(1500)  # вернуть всю Россию
    # 2. Правила ведения
    pg.click('#btn-rules-nav')
    pg.wait_for_timeout(6000)
    pg.keyboard.press('Escape')
    pg.wait_for_timeout(1200)
    # 3. Расчёт плана
    scroll_to(pg, '#rules-row', 200, 800)
    pg.click('#btn-demo1')
    pg.wait_for_selector('#plan-table table', timeout=60000)
    pg.wait_for_timeout(2500)
    scroll_to(pg, '#plan-table', 130, 5000)
    pg.evaluate("window.scrollBy({top: 420, behavior: 'smooth'})")
    pg.wait_for_timeout(5000)  # строка «Итого»
    scroll_to(pg, '#plan-periods', 130, 5000)                                                   # недели
    scroll_to(pg, '#plan-geo', 130, 4000)                                                       # карта бюджета
    scroll_to(pg, '#manual-block', 130, 2000)
    pg.click("#chan-sliders .sl:nth-child(4) input[type=range]", position={'x': 240, 'y': 10})
    pg.wait_for_timeout(4000)
    scroll_to(pg, '#plan-table', 130, 2500)
    # 4. Утверждение и кампания по часам
    pg.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    pg.wait_for_timeout(900)
    pg.click('#btn-approve2')
    pg.wait_for_selector('#btn-gorun', timeout=20000)
    pg.wait_for_timeout(1200)
    pg.click('#btn-gorun')
    pg.wait_for_selector('#btn-run', timeout=20000)
    pg.wait_for_timeout(1500)
    pg.click('#btn-run')
    pg.wait_for_selector('#controls', timeout=90000)
    pg.wait_for_timeout(2000)
    pg.click('#p-play')
    pg.wait_for_timeout(9000)  # плеер идёт до карточки
    pg.wait_for_timeout(3000)
    scroll_to(pg, '#pending-slot', 150, 6000)                           # карточка решения
    pg.click('#pending-slot button[data-d=approve]')
    pg.wait_for_selector('#decision-banner .verdict', timeout=90000)
    pg.wait_for_timeout(3500)
    pg.evaluate("showHour(241); focusStop(241)")
    pg.wait_for_timeout(6000)  # шок рынка
    pg.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    pg.wait_for_timeout(2500)
    pg.click('#p-end')
    pg.wait_for_timeout(4000)
    # 5. Итог
    pg.evaluate("showScreen('summary')")
    pg.wait_for_timeout(6000)
    pg.evaluate("window.scrollBy({top: 500, behavior: 'smooth'})")
    pg.wait_for_timeout(5000)
    # 6. Постановка B: цель, которой не достичь
    pg.click('.step[data-go=brief]')
    pg.wait_for_timeout(1500)
    scroll_to(pg, '#btn-demo2', 300, 1200)
    pg.click('#btn-demo2')
    # диагноз недостижимости считается дольше обычного плана: ждём сам экран отказа, а не время
    pg.wait_for_selector('#plan-body .moves', timeout=90000)
    pg.wait_for_timeout(2000)
    pg.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    pg.wait_for_timeout(5000)
    pg.evaluate("window.scrollBy({top: 420, behavior: 'smooth'})")
    pg.wait_for_timeout(7000)
    pg.evaluate("window.scrollBy({top: 380, behavior: 'smooth'})")
    pg.wait_for_timeout(7000)
    path = pg.video.path()
    ctx.close()
    b.close()
print('видео:', path, round(os.path.getsize(path)/1024/1024, 1), 'МБ')
