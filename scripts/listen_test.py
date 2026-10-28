"""Speak the same lines with F5 and Chatterbox.

The character decision is yours. This script only writes the wavs.
It needs voices/l/neutral.wav and voices/kira/neutral.wav, plus transcripts for F5.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import data_dir  # noqa: E402
from app.engines import EngineError  # noqa: E402
from app.synth import BusyError, synthesize  # noqa: E402
from app.voices import resolve  # noqa: E402

LINES = [
    ("l", "neutral", "The records do not match the story you told me."),
    ("kira", "neutral", "I was at home that night, the same as always."),
    ("l", "neutral", "Then the time on the receipt is a problem."),
    ("kira", "neutral", "Receipts can be wrong. People misplace them."),
]
ENGINES = ("f5", "chatterbox")


def main() -> None:
    missing = []
    for speaker in ("l", "kira"):
        try:
            resolve(speaker, "neutral")
        except FileNotFoundError as exc:
            missing.append(str(exc))
    if missing:
        raise SystemExit(
            "Add neutral clips before the listen test.\n"
            + "\n".join(missing)
            + "\npython scripts/prepare_clip.py --speaker l --emotion neutral "
            "--input clip.wav --transcript \"words in the clip\""
        )

    out_root = data_dir() / "listen"
    for engine in ENGINES:
        os.environ["SKIT_ENGINE"] = engine
        from app.engines.f5 import unload as unload_f5
        from app.engines.chatterbox import unload as unload_chatterbox

        unload_f5()
        unload_chatterbox()
        folder = out_root / engine
        folder.mkdir(parents=True, exist_ok=True)
        print(f"\n{engine}")
        try:
            for index, (speaker, emotion, text) in enumerate(LINES, start=1):
                clip = resolve(speaker, emotion)
                path, _key = synthesize(clip, text, emotion)
                dest = folder / f"{index}-{speaker}-{emotion}.wav"
                dest.write_bytes(path.read_bytes())
                fallback = " (neutral clip)" if clip.fell_back else ""
                print(f"  {dest.name}{fallback}")
        except (EngineError, BusyError, OSError, RuntimeError) as exc:
            print(f"  stopped: {exc}")
        finally:
            unload_f5()
            unload_chatterbox()

    print(
        "\nListen to data/listen/f5 and data/listen/chatterbox. "
        "Keep the engine that still sounds like the same person. "
        "Set SKIT_ENGINE in .env to f5 or chatterbox."
    )


if __name__ == "__main__":
    main()
