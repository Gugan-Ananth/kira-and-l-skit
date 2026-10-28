"""F5-TTS PyTorch for Windows. Weights come from disk. This module does not download them."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import numpy as np

from app.config import f5_device, f5_method, f5_model_path, f5_steps, vocos_path
from app.engines import EngineError
from app.progress import note_take, phase, report_step, step_index
from app.reference import (
    SAMPLE_RATE,
    generation_frames,
    match_reference_level,
    polish_output,
    prepare_reference,
    split_sentences,
)

_MODEL = None


def _load_soundfile():
    """Windows embeddable Python does not search a package folder for DLLs."""

    if os.name == "nt":
        import importlib.util

        spec = importlib.util.find_spec("soundfile")
        if spec and spec.origin:
            folder = Path(spec.origin).resolve().parent
            extras = [folder]
            data = folder / "_soundfile_data"
            if data.is_dir():
                extras.append(data)
            extras.extend(path.parent for path in folder.rglob("*.dll"))
            for extra in dict.fromkeys(extras):
                try:
                    os.add_dll_directory(str(extra))
                except (OSError, AttributeError):
                    pass
                current = os.environ.get("PATH", "")
                prefix = str(extra)
                if current.split(os.pathsep)[0] != prefix:
                    os.environ["PATH"] = prefix if not current else prefix + os.pathsep + current
    try:
        import soundfile as sf
    except (ImportError, OSError) as exc:
        raise EngineError("The audio library failed to load. " + str(exc)) from exc
    return sf


def _silence_cuda_cleanup() -> None:
    """F5 calls torch.cuda.empty_cache() after loading. The CPU wheel raises there."""

    import torch

    def _empty() -> None:
        return None

    try:
        torch.cuda.empty_cache = _empty
    except Exception:
        pass


def _use_bundled_ffmpeg() -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    try:
        from pydub import AudioSegment
    except ImportError:
        return
    AudioSegment.converter = ffmpeg
    probe = shutil.which("ffprobe")
    if probe:
        AudioSegment.ffprobe = probe


def _vocos_dir() -> Path:
    root = vocos_path()
    if (root / "config.yaml").is_file():
        return root
    found = next(root.rglob("config.yaml"), None)
    return found.parent if found is not None else root


def _ensure_vocos_bin(folder: Path) -> None:
    """F5 reads pytorch_model.bin. Some downloads only include a safetensors file."""

    if (folder / "pytorch_model.bin").is_file():
        return
    weights = next(folder.glob("*.safetensors"), None)
    if weights is None:
        return
    import torch
    from safetensors.torch import load_file

    torch.save(load_file(str(weights)), folder / "pytorch_model.bin")


class F5TorchEngine:
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
        ckpt = f5_model_path()
        vocoder = vocos_path()
        if not ckpt.is_file():
            raise EngineError(
                f"F5 weights not found at {ckpt}. "
                "The Windows installer places model_1250000.safetensors there."
            )
        if not vocoder.is_dir():
            raise EngineError(
                f"Vocos weights not found at {vocoder}. "
                "The Windows installer places the vocoder folder there."
            )
        if not ref_text.strip():
            raise EngineError(
                f"F5 needs the words spoken in {ref_wav.name}. "
                f"Add {ref_wav.with_suffix('.txt').name} beside that clip."
            )
        phase("loading")
        sf = _load_soundfile()

        audio, sample_rate = sf.read(ref_wav)
        if sample_rate != SAMPLE_RATE:
            raise EngineError("Reference clip must be 24 kHz. Run scripts/prepare_clip.py.")
        if getattr(audio, "ndim", 1) > 1:
            audio = np.mean(audio, axis=1)
        fitted, fitted_text = prepare_reference(
            np.asarray(audio, dtype=np.float32), sample_rate, ref_text
        )
        if not fitted_text.strip():
            raise EngineError(f"F5 needs the words spoken in {ref_wav.name}.")
        # The sampler was trained around this level. A quiet clip otherwise
        # comes back quieter than the Mac take.
        fitted = match_reference_level(fitted)
        phase("preparing")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="skit-ref-") as folder:
            ref_path = Path(folder) / "ref.wav"
            raw_path = Path(folder) / "out.wav"
            sf.write(ref_path, fitted, SAMPLE_RATE)
            model = _load()
            _use_bundled_ffmpeg()
            try:
                _infer(model, ref_path, fitted_text, text, raw_path)
            except EngineError:
                raise
            except Exception as exc:
                raise EngineError(f"{type(exc).__name__}: {exc}") from exc
            if not raw_path.is_file():
                raise EngineError("F5 did not write audio for that line.")
            spoken, spoken_rate = sf.read(raw_path)
        if getattr(spoken, "ndim", 1) > 1:
            spoken = np.mean(spoken, axis=1)
        sf.write(
            out_path,
            polish_output(np.asarray(spoken, dtype=np.float32), int(spoken_rate)),
            int(spoken_rate),
        )


def torch_ode_method(method: str) -> str:
    """Keep rk4. Euler on the Mac step count turns the rest of a line into noise."""

    if method in {"euler", "midpoint", "rk4"}:
        return method
    return "rk4"


def configure_cpu_threads() -> None:
    """Let one line use the cores. Extra inter-op threads fight the sampler."""

    if os.name != "nt":
        return
    count = max(1, os.cpu_count() or 1)
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(key, str(count))
    os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
    try:
        import torch
    except ImportError:
        return
    torch.set_num_threads(count)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass


# The reference is about 10s and is inside every solve. One solve per sentence
# repeats it. A normal line fits in one rk4 pass. Past this, the next sentence
# starts a new pass. The passes are placed end to end, with no crossfade.
MAX_TAKE_SECONDS = 30.0


def plan_takes(ref_samples: int, ref_text: str, text: str) -> list[tuple[str, int]]:
    """Group sentences into rk4 passes. Each pass is (spoken text, frame count)."""

    sentences = [sentence for sentence in split_sentences(text) if sentence]
    if not sentences:
        return []
    max_frames = int(MAX_TAKE_SECONDS * SAMPLE_RATE / 256)
    groups: list[list[str]] = []
    current: list[str] = []
    for sentence in sentences:
        trial = [*current, sentence]
        frames = generation_frames(ref_samples, ref_text, " ".join(trial))
        if current and frames > max_frames:
            groups.append(current)
            current = [sentence]
        else:
            current = trial
    if current:
        groups.append(current)
    takes = []
    for group in groups:
        spoken = " ".join(group)
        takes.append((spoken, generation_frames(ref_samples, ref_text, spoken)))
    return takes


def speak_sentences(sample_sentence, ref_samples: int, ref_text: str, text: str) -> np.ndarray:
    """Speak the line in as few rk4 passes as fit, and place those passes end to end.

    The stock Windows sampler splits on commas, runs those pieces at the same
    time, and crossfades the joins. That overlap is the noise in the middle
    of a line. A pass never overlaps the next one.
    """

    waves = []
    takes = plan_takes(ref_samples, ref_text, text)
    for index, (spoken, duration) in enumerate(takes, start=1):
        note_take(index, len(takes))
        wave = np.asarray(sample_sentence(spoken, duration), dtype=np.float32).reshape(-1)
        if wave.size == 0 or not np.isfinite(wave).all():
            raise EngineError("F5 did not write audio for that line.")
        waves.append(wave)
    if not waves:
        raise EngineError("F5 did not write audio for that line.")
    return np.concatenate(waves)


def _sample_sentence(model, samples: np.ndarray, ref_text: str, sentence: str, duration: int) -> np.ndarray:
    import torch
    from f5_tts.model.utils import convert_char_to_pinyin

    hop = 256
    audio = torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32)).reshape(-1)
    audio = audio.unsqueeze(0).to(model.device)
    prompt = convert_char_to_pinyin([f"{ref_text.strip()} {sentence}"])
    transformer = getattr(model.ema_model, "transformer", None)
    if transformer is not None and hasattr(transformer, "clear_cache"):
        # sample() keeps the text embedding until it returns. Drop it first so
        # a line that failed halfway cannot be reused for the next sentence.
        transformer.clear_cache()
    steps = f5_steps()
    restore = _track_solver()
    try:
        with torch.inference_mode():
            generated, _ = model.ema_model.sample(
                cond=audio,
                text=prompt,
                duration=int(duration),
                steps=steps,
                cfg_strength=2.0,
                sway_sampling_coef=-1.0,
                seed=0,
            )
            ref_frames = int(audio.shape[-1] // hop)
            mel = generated.to(torch.float32)[:, ref_frames:, :]
            mel = mel.permute(0, 2, 1)
            wave = model.vocoder.decode(mel)
    finally:
        restore()
    return np.asarray(wave.detach().float().cpu().numpy(), dtype=np.float32).reshape(-1)


def _clock(value) -> float:
    if hasattr(value, "detach"):
        value = value.detach().reshape(-1)[0]
    return float(value)


def _track_solver():
    """Report each solver step. The sample itself is unchanged."""

    import f5_tts.model.cfm as cfm

    original = cfm.odeint

    def tracked(func, y0, t, **kwargs):
        grid = [_clock(mark) for mark in t.detach().reshape(-1)]

        def wrapped(t_now, y):
            try:
                report_step(step_index(_clock(t_now), grid), max(1, len(grid) - 1))
            except Exception:
                pass
            return func(t_now, y)

        return original(wrapped, y0, t, **kwargs)

    cfm.odeint = tracked

    def restore() -> None:
        cfm.odeint = original

    return restore


def _infer(model, ref_path: Path, fitted_text: str, text: str, raw_path: Path) -> None:
    sf = _load_soundfile()
    samples, sample_rate = sf.read(ref_path)
    if int(sample_rate) != SAMPLE_RATE:
        raise EngineError("Reference clip must be 24 kHz.")
    if getattr(samples, "ndim", 1) > 1:
        samples = np.mean(samples, axis=1)
    samples = np.ascontiguousarray(samples, dtype=np.float32)

    def sample_sentence(sentence: str, duration: int) -> np.ndarray:
        return _sample_sentence(model, samples, fitted_text, sentence, duration)

    spoken = speak_sentences(sample_sentence, int(samples.shape[0]), fitted_text, text)
    sf.write(raw_path, spoken, SAMPLE_RATE)


def _load():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    configure_cpu_threads()
    from app.desktop import prepare_windows_dlls

    prepare_windows_dlls()
    _silence_cuda_cleanup()
    try:
        from f5_tts.api import F5TTS
    except ImportError as exc:
        raise EngineError(
            "PyTorch F5 is not installed. On Windows run packaging/windows/build.ps1."
        ) from exc
    except Exception as exc:
        raise EngineError(f"{type(exc).__name__}: {exc}") from exc
    ode_method = torch_ode_method(f5_method())
    vocoder = _vocos_dir()
    try:
        _ensure_vocos_bin(vocoder)
        _silence_cuda_cleanup()
        _MODEL = F5TTS(
            model="F5TTS_v1_Base",
            ckpt_file=str(f5_model_path()),
            vocoder_local_path=str(vocoder),
            ode_method=ode_method,
            device=f5_device(),
        )
    except EngineError:
        raise
    except Exception as exc:
        raise EngineError(f"{type(exc).__name__}: {exc}") from exc
    return _MODEL


def unload() -> None:
    global _MODEL
    _MODEL = None
