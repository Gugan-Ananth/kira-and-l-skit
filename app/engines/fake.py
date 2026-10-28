"""Silent wav writer used by tests. It does not load a speech model."""

from __future__ import annotations

import math
import wave
from pathlib import Path


class FakeEngine:
    name = "fake"

    def synthesize(
        self,
        *,
        text: str,
        ref_wav: Path,
        ref_text: str,
        emotion: str,
        out_path: Path,
    ) -> None:
        del ref_wav, ref_text
        out_path.parent.mkdir(parents=True, exist_ok=True)
        seconds = min(2.0, 0.4 + 0.04 * max(1, len(text.split())))
        rate = 24000
        frames = int(rate * seconds)
        # A quiet tone so the file is valid audio, not a character voice.
        frequency = 220 if emotion == "neutral" else 180 + (sum(emotion.encode()) % 80)
        with wave.open(str(out_path), "w") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            pcm = bytearray()
            for index in range(frames):
                sample = int(1200 * math.sin(2 * math.pi * frequency * index / rate))
                pcm += int(sample).to_bytes(2, "little", signed=True)
            handle.writeframes(bytes(pcm))
