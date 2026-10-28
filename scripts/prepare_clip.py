"""Turn one clip into a 24 kHz mono wav plus a transcript sidecar.

Does not separate music or a second speaker. You supply a clip you are allowed to use.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.audio import AudioError, run_ffmpeg  # noqa: E402
from app.config import EMOTIONS, SPEAKERS, voices_dir  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare one reference clip.")
    parser.add_argument("--speaker", required=True, choices=SPEAKERS)
    parser.add_argument("--emotion", required=True, choices=EMOTIONS)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--transcript", required=True, help="Exact words spoken in the clip.")
    parser.add_argument("--seconds", type=float, default=10, help="Trim length. Default 10.")
    args = parser.parse_args()

    if not args.input.is_file():
        raise SystemExit(f"No file at {args.input}")
    if args.seconds <= 0 or args.seconds > 30:
        raise SystemExit("Seconds must be between 0 and 30.")

    folder = voices_dir() / args.speaker
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{args.emotion}.wav"
    try:
        run_ffmpeg(
            [
                "-y",
                "-i",
                str(args.input),
                "-ac",
                "1",
                "-ar",
                "24000",
                "-t",
                f"{args.seconds:.3f}",
                "-c:a",
                "pcm_s16le",
                str(dest),
            ]
        )
    except AudioError as exc:
        raise SystemExit(str(exc)) from exc
    dest.with_suffix(".txt").write_text(args.transcript.strip() + "\n", encoding="utf-8")
    print(f"Wrote {dest}")
    print(f"Wrote {dest.with_suffix('.txt')}")


if __name__ == "__main__":
    main()
