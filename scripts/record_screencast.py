"""Скринкаст кабинета с озвучкой: бриф → план → кампания → итог → отказ.

Артефакт по требованию задания: короткое видео на 2–5 минут. Кабинет снимает
Playwright, реплики читает `ru-RU-DmitryNeural` (edge-tts), субтитры рисует сам
кабинет — скрипт подкладывает их в разметку, поэтому шрифты и цвета те же, что
на экране.

Текст реплик живёт в `docs/voiceover.md`, здесь только действия сцен. Порядок
работы: сначала синтез и замер длительностей, потом запись (сцена не короче
своей реплики), потом сборка дорожки с тишиной по фактическим длительностям
сцен и мультиплексирование. Так звук не разъезжается с картинкой, даже если
расчёт кампании занял больше времени, чем реплика.

Нужны Playwright с chromium, edge-tts и ffmpeg (в зависимости проекта не
входят):

    pip install playwright edge-tts && playwright install chromium
    uvicorn app.main:app --port 8001          # кабинет, который снимаем
    python scripts/record_screencast.py       # docs/screencast.mp4
"""

from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "screencast"
VOICEOVER = ROOT / "docs" / "voiceover.md"
VOICE = "ru-RU-DmitryNeural"
PITCH = "-8Hz"
RATE = "-5%"
ACUTE = "́"  # комбинирующее ударение: движок понимает его, знак «+» читает вслух
VOWELS = "аеёиоуыэюя"
TAIL = 0.8  # сколько секунд сцена держится после реплики


# --------------------------------------------------------------- текст и синтез

def read_scenes() -> dict[str, dict[str, str]]:
    """Сцены из docs/voiceover.md: идентификатор → субтитр и текст для синтеза."""
    text = VOICEOVER.read_text(encoding="utf-8")
    scenes: dict[str, dict[str, str]] = {}
    for block in re.split(r"\n### ", text)[1:]:
        name, body = block.split("\n", 1)
        subtitle = re.search(r"\*\*Субтитр\.\*\*(.+?)(?=\n\n)", body, re.S)
        speech = re.search(r"\*\*Озвучка\.\*\*(.+?)(?=\n\n|$)", body, re.S)
        if not (subtitle and speech):
            raise SystemExit(f"сцена {name.strip()}: нет субтитра или озвучки")
        scenes[name.strip()] = {
            "subtitle": " ".join(subtitle.group(1).split()),
            "speech": " ".join(speech.group(1).split()),
        }
    return scenes


def for_engine(speech: str) -> str:
    """«+а» из текста → «а» с ударением: в файле знак стоит перед гласной."""
    for match in re.finditer(r"\+(.)", speech):
        if match.group(1).lower() not in VOWELS:
            around = speech[max(0, match.start() - 20):match.end() + 10]
            raise SystemExit(f"ударение не перед гласной: …{around}…")
    return re.sub(r"\+(.)", lambda m: m.group(1) + ACUTE, speech)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def synthesize(scenes: dict[str, dict[str, str]], order: list[str]) -> dict[str, float]:
    """Озвучиваем реплики и возвращаем длительность каждой в секундах."""
    import edge_tts

    BUILD.mkdir(parents=True, exist_ok=True)
    lines = {name: for_engine(scenes[name]["speech"]) for name in order}  # разбор до синтеза

    async def say_all() -> None:
        for name in order:
            for attempt in range(4):  # сервис синтеза иногда отдаёт пустой ответ
                try:
                    speaker = edge_tts.Communicate(lines[name], VOICE, rate=RATE, pitch=PITCH)
                    await speaker.save(str(BUILD / f"{name}.mp3"))
                    break
                except Exception as err:
                    if attempt == 3:
                        raise SystemExit(f"не озвучили сцену {name}: {err}") from None
                    print(f"  {name}: попытка {attempt + 1} не удалась, повторяем")
                    await asyncio.sleep(3)

    asyncio.run(say_all())
    lengths = {}
    for name in order:
        lengths[name] = duration(BUILD / f"{name}.mp3")
        print(f"  {name}: {lengths[name]:.1f} с")
    return lengths


# ------------------------------------------------------------------ действия сцен

def scroll_to(pg, selector: str, offset: int = 130, pause: int = 900) -> None:
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


def to_top(pg, pause: int = 900) -> None:
    pg.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    pg.wait_for_timeout(pause)


def act_intro(pg, base: str) -> None:
    pg.goto(f"{base}/?role=manager")
    pg.wait_for_selector("#geo-map g[data-district]")
    pg.wait_for_timeout(3000)
    scroll_to(pg, "#channel-chips", 220, 3500)  # восемь каналов из реплики — на экране
    to_top(pg, 1500)


def act_brief(pg, _base: str) -> None:
    pg.click("#budget")
    pg.fill("#budget", "")
    pg.type("#budget", "1200000", delay=90)
    pg.wait_for_timeout(1200)
    scroll_to(pg, "#f-target", 120, 1200)
    for key in ("25_34", "35_44"):  # сузили сегмент — видно долю аудитории
        pg.click(f".tag[data-ax=age_groups][data-k='{key}']")
        pg.wait_for_timeout(1100)
    pg.wait_for_timeout(1400)
    for key in ("25_34", "35_44"):
        pg.click(f".tag[data-ax=age_groups][data-k='{key}']")
    # пресеты географии, а не клики по карте: у «всей России» выбрано всё, и клик
    # по округу читался бы как снятие, а не как выбор
    pg.click("#geo-presets button:nth-child(4)")  # только Центральный
    pg.wait_for_timeout(2200)
    pg.click("#geo-presets button:nth-child(2)")  # европейская часть
    pg.wait_for_timeout(2200)
    pg.click("#geo-presets button:nth-child(1)")  # вся Россия
    pg.wait_for_timeout(1500)


def caption_host(pg, into_dialog: bool) -> None:
    """Субтитр живёт в верхнем слое, пока открыто модальное окно, иначе — в body."""
    pg.evaluate(
        """(into) => {
            const bar = document.querySelector('#screencast-caption');
            const dlg = document.querySelector('dialog[open]');
            if (bar) (into && dlg ? dlg : document.body).appendChild(bar);
        }""",
        into_dialog,
    )


def act_rules(pg, _base: str) -> None:
    scroll_to(pg, "#rules-row", 220, 900)
    pg.click("#btn-rules")
    caption_host(pg, True)
    pg.wait_for_timeout(1200)
    pg.click("#auto_limit")  # лимит из реплики виден на экране, а не только в демо-кнопке
    pg.type("#auto_limit", "50000", delay=120)
    pg.wait_for_timeout(3800)
    # окно выше экрана: доезжаем до порогов молчания, о которых вторая половина реплики
    pg.evaluate("() => document.querySelector('#rules-modal').scrollTo({top: 9999, behavior: 'smooth'})")
    pg.wait_for_timeout(6500)
    caption_host(pg, False)
    pg.click("#btn-rules-ok")
    pg.wait_for_timeout(2500)  # строка правил на брифе пересобралась под новый лимит


def act_plan(pg, _base: str) -> None:
    to_top(pg, 500)
    pg.click("#btn-demo1")
    pg.wait_for_selector("#plan-table table", timeout=60000)
    pg.wait_for_timeout(7500)  # плитки: прогноз, разброс, бюджет
    scroll_to(pg, "#plan-table", 150, 15000)  # таблица каналов с ценой следующей конверсии


def act_periods(pg, _base: str) -> None:
    scroll_to(pg, "#plan-periods", 130, 3800)
    pg.click("#periods-seg button[data-p=days]")
    pg.wait_for_timeout(4200)
    scroll_to(pg, "#plan-geo", 130, 4500)


def act_manual(pg, _base: str) -> None:
    scroll_to(pg, "#manual-block", 130, 1200)
    bar = pg.query_selector("#chan-sliders .sl:nth-child(4) input[type=range]")
    if bar is None:
        raise SystemExit("нет бегунков ручной правки — сцену снимать не на чем")
    box = bar.bounding_box()
    pg.mouse.click(box["x"] + box["width"] * 0.62, box["y"] + box["height"] / 2)
    pg.wait_for_selector("#chan-sliders .sl.fixed", timeout=60000)
    note = pg.text_content("#manual-note") or ""
    if "не принимает" not in note:  # реплика говорит про срез по ёмкости — он должен случиться
        raise SystemExit(f"фиксация не упёрлась в ёмкость, реплика разойдётся с экраном: {note[:120]}")
    # пересчёт плана возвращает страницу наверх сам: там и виден новый прогноз
    pg.wait_for_timeout(5500)
    scroll_to(pg, "#manual-note", 300, 7000)  # чем обернулась фиксация — словами
    pg.click("#btn-manual-reset")  # правка отменена: дальше утверждаем расчётный план
    pg.wait_for_selector("#btn-manual-reset[disabled]", timeout=60000)
    pg.wait_for_timeout(5500)


def settled(pg) -> None:
    """Ждём, пока кабинет не пересчитывает прогон: правило могло применить перенос само."""
    pg.wait_for_function("() => !DECIDING && !AUTO_BUSY", timeout=180000)


def act_run(pg, _base: str) -> None:
    scroll_to(pg, "#plan-foot", 320, 900)
    pg.click("#btn-approve2")
    pg.wait_for_selector("#btn-gorun2", timeout=60000)
    pg.wait_for_timeout(1200)
    pg.click("#btn-gorun2")
    pg.wait_for_selector("#btn-run", state="visible", timeout=30000)
    pg.wait_for_timeout(1500)
    pg.click("#btn-run")
    pg.wait_for_selector("#controls", timeout=180000)  # прогон считает два прохода
    pg.wait_for_timeout(1500)
    pg.click("#p-play")
    pg.wait_for_timeout(6000)
    scroll_to(pg, "#digest", 200, 4000)  # сводка часа: прошло, потрачено, получено


def act_shock(pg, _base: str) -> None:
    hour = pg.evaluate("() => (RUN.scenarioHours || [])[0] || 241")
    # до часа шока кабинет доезжает не сразу: ранние карточки никто не решал, и правило
    # применяет их само, каждый раз пересчитывая прогон и сдвигая плеер на свой стоп.
    # Причину остановки пишем только встав на нужный час, иначе на экране будет
    # баннер про шок рынка над часами шестого дня
    for _ in range(14):
        settled(pg)
        if pg.evaluate("() => PLAYER.h") == hour:
            break
        pg.evaluate(
            """(hour) => {
                const s = document.querySelector('#p-slider');
                s.value = hour; s.dispatchEvent(new Event('input'));
            }""",
            hour,
        )
        pg.wait_for_timeout(800)  # правило успевает взяться за карточку, дальше ждёт settled
    if pg.evaluate("() => PLAYER.h") != hour:
        raise SystemExit(f"плеер не встал на час шока {hour}: кампания упёрлась в ждущую карточку")
    pg.evaluate("(hour) => focusStop(hour)", hour)
    pg.wait_for_timeout(300)
    banner = pg.text_content("#decision-banner") or ""
    if "событие" not in banner:
        raise SystemExit(f"кабинет не написал причину остановки: {banner[:120]}")
    pg.wait_for_timeout(6500)
    settled(pg)
    pg.click("#p-next")  # следующая остановка — детектор и перенос из мёртвого канала
    settled(pg)
    if pg.evaluate("() => PLAYER.h") <= hour:
        raise SystemExit("после часа шока плеер не сдвинулся: последствий в кадре не будет")
    scroll_to(pg, "#feed-short", 200, 6500)  # события часа словами


def act_card(pg, _base: str) -> None:
    card = "#pending-slot button[data-h][data-d=approve]"
    for _ in range(10):  # плеер встаёт и на событиях рынка: доходим до карточки
        if pg.query_selector(card):
            break
        settled(pg)
        pg.click("#p-next")
        pg.wait_for_timeout(1300)
    if not pg.query_selector(card):
        raise SystemExit("карточка решения не появилась — снимать сцену не на чем")
    scroll_to(pg, "#pending-slot", 290, 9500)  # читаем карточку целиком, потом решаем
    pg.click(card)
    settled(pg)
    pg.wait_for_timeout(7000)


def act_summary(pg, _base: str) -> None:
    for _ in range(40):  # «К концу кампании» упирается в ждущие карточки: правило их решает
        if pg.evaluate("() => !!(RUN && RUN._ended)"):
            break
        settled(pg)
        to_top(pg, 300)
        pg.click("#p-end")
        pg.wait_for_timeout(1300)
    if not pg.evaluate("() => !!(RUN && RUN._ended)"):
        raise SystemExit("кампания не дошла до конца — итога нет")
    cta = pg.query_selector("#run-done-cta button")
    if cta is None:
        raise SystemExit("кнопки «К итогу» нет, хотя кампания закончилась")
    cta.click()
    pg.wait_for_selector("#summary-body .stats", timeout=30000)
    pg.wait_for_timeout(9000)  # «Коротко»: обещали и получили
    pg.evaluate("window.scrollBy({top: 180, behavior: 'smooth'})")
    pg.wait_for_timeout(10000)  # плитки: обещание, цена по факту, возврат, что дало ведение


def act_refusal(pg, base: str) -> None:
    # вторая постановка — отдельная история: открываем кабинет заново. Так не мешает
    # утверждённый план (кабинет спросил бы подтверждение) и не тянется состояние прогона
    reload_cabinet(pg, base)
    scroll_to(pg, "#btn-demo2", 300, 900)
    pg.click("#btn-demo2")
    # диагноз недостижимости считается дольше обычного плана: ждём экран, а не время
    pg.wait_for_selector("#plan-body .moves", timeout=180000)
    to_top(pg, 9500)  # максимум при этих условиях и во что упирается
    scroll_to(pg, "#plan-body .moves", 200, 10500)


SCENES = [
    ("intro", act_intro), ("brief", act_brief), ("rules", act_rules), ("plan", act_plan),
    ("periods", act_periods), ("manual", act_manual), ("run", act_run),
    ("shock", act_shock), ("card", act_card), ("summary", act_summary),
    ("refusal", act_refusal),
]

CAPTION_CSS = """
#screencast-caption {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 9999;
  background: rgba(32,30,29,.93); color: #f4f3f2; padding: 15px 32px;
  font-family: 'Golos Text', system-ui, sans-serif; font-size: 19px; line-height: 1.35;
  text-align: center; letter-spacing: .01em;
}
"""


LAST_CAPTION = ""


def show_caption(pg, text: str) -> None:
    global LAST_CAPTION
    LAST_CAPTION = text
    pg.evaluate(
        """(text) => {
            let bar = document.querySelector('#screencast-caption');
            if (!bar) { bar = document.createElement('div'); bar.id = 'screencast-caption'; }
            const host = document.querySelector('dialog[open]') || document.body;
            if (bar.parentNode !== host) host.appendChild(bar);
            bar.textContent = text;
        }""",
        text,
    )


def reload_cabinet(pg, base: str) -> None:
    """Открываем кабинет заново; стиль и субтитр живут в документе, поэтому их возвращаем."""
    pg.goto(f"{base}/?role=manager")
    pg.wait_for_selector("#geo-map g[data-district]")
    pg.add_style_tag(content=CAPTION_CSS)
    show_caption(pg, LAST_CAPTION)
    pg.wait_for_timeout(900)


# ------------------------------------------------------------------------ сборка

def build_audio(order: list[str], actual: dict[str, float]) -> Path:
    """Дорожка из реплик с тишиной до фактической длины каждой сцены."""
    parts = []
    for name in order:
        wav = BUILD / f"{name}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(BUILD / f"{name}.mp3"),
             "-af", f"apad=whole_dur={actual[name]:.3f}", "-t", f"{actual[name]:.3f}",
             "-ar", "44100", "-ac", "2", str(wav)],
            check=True,
        )
        parts.append(wav)
    listing = BUILD / "audio.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    track = BUILD / "voice.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", str(track)],
        check=True, cwd=str(BUILD),
    )
    return track


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8001")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "screencast.mp4")
    args = parser.parse_args()

    scenes = read_scenes()
    missing = [name for name, _ in SCENES if name not in scenes]
    if missing:
        raise SystemExit(f"в docs/voiceover.md нет сцен: {', '.join(missing)}")

    print("озвучка:")
    lengths = synthesize(scenes, [name for name, _ in SCENES])

    video_dir = BUILD / "video"
    shutil.rmtree(video_dir, ignore_errors=True)
    video_dir.mkdir(parents=True, exist_ok=True)
    actual: dict[str, float] = {}
    print("запись:")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800}, record_video_dir=str(video_dir),
            record_video_size={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        # на пересчёт утверждённого плана кабинет спрашивает подтверждение: отвечаем «да»
        page.on("dialog", lambda d: d.accept())
        for name, action in SCENES:
            started = time.perf_counter()
            if name == "intro":  # до загрузки страницы субтитр вставлять некуда
                action(page, args.base)
                page.add_style_tag(content=CAPTION_CSS)
                show_caption(page, scenes[name]["subtitle"])
            else:
                show_caption(page, scenes[name]["subtitle"])
                action(page, args.base)
            spare = lengths[name] + TAIL - (time.perf_counter() - started)
            if spare > 0:
                page.wait_for_timeout(int(spare * 1000))
            actual[name] = time.perf_counter() - started
            print(f"  {name}: реплика {lengths[name]:.1f} с, сцена {actual[name]:.1f} с")
        raw = page.video.path()
        ctx.close()
        browser.close()

    track = build_audio([name for name, _ in SCENES], actual)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-i", str(track),
         "-c:v", "libx264", "-crf", "30", "-preset", "slow", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-shortest", str(args.out)],
        check=True,
    )
    total = duration(args.out)
    size = args.out.stat().st_size / 1024 / 1024
    print(f"готово: {args.out} — {int(total // 60)}:{int(total % 60):02d}, {size:.1f} МБ")


if __name__ == "__main__":
    main()
