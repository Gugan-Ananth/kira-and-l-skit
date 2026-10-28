"""F5-TTS MLX, 4-bit. Delivery follows the reference clip. Apple Silicon only."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.config import f5_method, f5_steps
from app.engines import EngineError
from app.progress import note_take, phase
from app.reference import (
    SAMPLE_RATE,
    generation_frames,
    match_reference_level,
    polish_output,
    prepare_reference,
    split_sentences,
)

_MODEL = None


class F5Engine:
    name = "f5"

    def synthesize(
        self,
        *,
        text: str,
        ref_wav: Path,
        ref_text: str,
        emotion: str,
        out_path: Path,
    ) -> None:
        del emotion
        phase("loading")
        if not ref_text.strip():
            raise EngineError(
                f"F5 needs the words spoken in {ref_wav.name}. "
                f"Add {ref_wav.with_suffix('.txt').name} beside that clip."
            )
        try:
            import mlx.core as mx
            import soundfile as sf
            from f5_tts_mlx.cfm import F5TTS
            from f5_tts_mlx.utils import convert_char_to_pinyin
        except ImportError as exc:
            raise EngineError(
                "F5 is not installed. Run: pip install -r requirements-engines.txt"
            ) from exc

        model = _load(F5TTS)
        audio, sample_rate = sf.read(ref_wav)
        if sample_rate != SAMPLE_RATE:
            raise EngineError("Reference clip must be 24 kHz. Run scripts/prepare_clip.py.")
        if getattr(audio, "ndim", 1) > 1:
            audio = np.mean(audio, axis=1)
        audio, ref_text = prepare_reference(np.asarray(audio, dtype=np.float32), sample_rate, ref_text)
        if not ref_text.strip():
            raise EngineError(f"F5 needs the words spoken in {ref_wav.name}.")
        audio = match_reference_level(audio)
        phase("preparing")
        audio = mx.array(audio)

        waves = []
        sentences = split_sentences(text)
        # mlx 0.32 rejects mlx scalars inside a shape tuple. f5-tts-mlx 0.2.6
        # still passes those scalars to mx.random.normal.
        original_normal = mx.random.normal

        def normal(shape=(), *args, **kwargs):
            if isinstance(shape, (list, tuple)):
                shape = tuple(int(dim) for dim in shape)
            return original_normal(shape, *args, **kwargs)

        mx.random.normal = normal
        try:
            for index, sentence in enumerate(sentences, start=1):
                note_take(index, len(sentences))
                prompt = convert_char_to_pinyin([f"{ref_text.strip()} {sentence}"])
                wave, _ = model.sample(
                    mx.expand_dims(audio, axis=0),
                    text=prompt,
                    duration=generation_frames(int(audio.shape[0]), ref_text, sentence),
                    steps=f5_steps(),
                    method=f5_method(),
                    cfg_strength=2.0,
                    sway_sampling_coef=-1.0,
                    seed=0,
                )
                wave = wave[audio.shape[0] :]
                mx.eval(wave)
                waves.append(np.array(wave).reshape(-1))
        finally:
            mx.random.normal = original_normal

        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(out_path, polish_output(np.concatenate(waves), SAMPLE_RATE), SAMPLE_RATE)


def _load(factory):
    global _MODEL
    if _MODEL is None:
        _MODEL = factory.from_pretrained("lucasnewman/f5-tts-mlx", quantization_bits=4)
    return _MODEL


def unload() -> None:
    global _MODEL
    _MODEL = None
