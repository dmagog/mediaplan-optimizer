"""Прослушать варианты произношения одной строки и выбрать из них на слух.

Синтез читает не то, что написано, а то, что разобрал: латиница, аббревиатуры
и заимствования выходят по-разному в зависимости от того, как их записать
по-русски. Спорить об этом на бумаге бессмысленно — надо слушать. Скрипт берёт
варианты записи одной и той же фразы, читает каждый и склеивает в один файл,
объявляя номера, чтобы можно было сказать «второй» и не пересылать файлы.

    python scripts/try_voice.py "Медиапл+ан оптим+айзер" "М+едиа-пл+ан оптим+айзер"
    python scripts/try_voice.py --manner веско --file build/variants.txt

Знак `+` ставится перед ударной гласной — как в `docs/voiceover.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

GAP = 0.7  # пауза между вариантами, чтобы номер не налезал на фразу


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("variants", nargs="*", help="варианты записи одной фразы")
    parser.add_argument("--file", type=Path, help="файл с вариантами, по одному на строку")
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "screencast" / "audition.mp3")
    parser.add_argument("--manner", default="ровно", help="подача: ровно, веско, живее, тише")
    parser.add_argument("--voice")
    args = parser.parse_args()

    from record_screencast import (
        MANNER,
        PITCH,
        RATE,
        VOICE,
        concat,
        for_engine,
        silence,
        synth_fragment,
        trim_to_wav,
    )

    if args.manner not in MANNER:
        raise SystemExit(f"подача «{args.manner}» неизвестна; есть: {', '.join(MANNER)}")
    style = MANNER[args.manner]
    voice = args.voice or VOICE

    variants = list(args.variants)
    if args.file:
        variants += [s.strip() for s in args.file.read_text(encoding="utf-8").splitlines() if s.strip()]
    if not variants:
        raise SystemExit("нечего слушать: передайте варианты аргументами или --file")

    work = args.out.parent / "audition"
    work.mkdir(parents=True, exist_ok=True)
    spoken = [(i, f"Вариант {i}.", for_engine(v)) for i, v in enumerate(variants, 1)]

    async def read_all() -> None:
        for i, label, body in spoken:
            await synth_fragment(label, work / f"{i:02d}-label.mp3", voice, RATE, PITCH)
            await synth_fragment(body, work / f"{i:02d}-body.mp3", voice, **style)

    asyncio.run(read_all())

    pieces = []
    for i, _, _ in spoken:  # номер, пауза, фраза, пауза подольше — чтобы не сливалось
        # тишину по краям срезаем той же функцией, что и сборка: иначе паузы между
        # вариантами на слух втрое больше заказанных и сравнивать нечего
        pieces.append(trim_to_wav(work / f"{i:02d}-label.mp3", work / f"{i:02d}-label.wav")[0])
        pieces.append(silence(GAP, work / f"{i:02d}-gap-a.wav"))
        pieces.append(trim_to_wav(work / f"{i:02d}-body.mp3", work / f"{i:02d}-body.wav")[0])
        pieces.append(silence(GAP * 2, work / f"{i:02d}-gap-b.wav"))
    track = concat(pieces, work / "audition.wav")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(track), "-b:a", "128k", str(args.out)],
        check=True,
    )
    for i, variant in enumerate(variants, 1):
        print(f"  {i}. {variant}")
    print(f"подача: {args.manner}, голос: {voice}")
    print(f"готово: {args.out}")


if __name__ == "__main__":
    main()
