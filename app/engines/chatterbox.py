"""Chatterbox MLX 4-bit. Exaggeration changes intensity. The clip sets the voice."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.config import (
    CHATTERBOX_EXAGGERATION,
    CHATTERBOX_TURBO_TAGS,
    chatterbox_model,
)
from app.engines import EngineError

_MODEL = None


class ChatterboxEngine:
    name = "chatterbox"

    def synthesize(
        self,
        *,
        text: str,
        ref_wav: Path,
        ref_text: str,
        emotion: str,
        out_path: Path,
    ) -> None:
        del ref_text
        try:
            import soundfile as sf
            from mlx_audio.tts.utils import load_model
        except ImportError as exc:
            raise EngineError(
                "Chatterbox is not installed. Run: pip install -r requirements-engines.txt"
            ) from exc

        model_id = chatterbox_model()
        spoken = text.strip()
        if "turbo" in model_id.lower():
            spoken = f"{CHATTERBOX_TURBO_TAGS.get(emotion, '')}{spoken}"
        exaggeration = CHATTERBOX_EXAGGERATION.get(emotion, 0.25)

        model = _load(load_model, model_id)
        results = list(
            model.generate(
                spoken,
                ref_audio=str(ref_wav),
                exaggeration=exaggeration,
                cfg_weight=0.5,
                lang_code="en",
            )
        )
        if not results:
            raise EngineError("Chatterbox returned no audio.")
        audio = np.array(results[0].audio).reshape(-1)
        sample_rate = int(getattr(results[0], "sample_rate", None) or model.sample_rate)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(out_path, audio, sample_rate)


def _load(loader, model_id: str):
    global _MODEL
    if _MODEL is None or getattr(_MODEL, "_skit_model_id", None) != model_id:
        model = loader(model_id)
        model._skit_model_id = model_id
        _MODEL = model
    return _MODEL


def unload() -> None:
    global _MODEL
    _MODEL = None
