"""One synthesis at a time, with a cache keyed by text, emotion, and clip."""

from __future__ import annotations

import hashlib
import queue
import threading
from pathlib import Path

from app.audio import raise_to_rms, to_wav_24k_mono
from app.config import data_dir, engine_name, speaker_output_rms
from app.engines import EngineError, build
from app.progress import clear as clear_progress
from app.progress import phase as progress_phase
from app.progress import start as start_progress
from app.voices import ResolvedClip

_LOCK = threading.Lock()
_ENGINE = None
_ENGINE_NAME: str | None = None
_MODEL_QUEUE: queue.Queue | None = None
_MODEL_THREAD: threading.Thread | None = None
_MODEL_GATE = threading.Lock()


class BusyError(RuntimeError):
    pass


def _model_loop(jobs: queue.Queue) -> None:
    while True:
        job = jobs.get()
        if job is None:
            return
        work, done = job
        try:
            done.put((True, work()))
        except BaseException as exc:
            done.put((False, exc))


def _on_model_thread(work):
    """Run speech work on the one thread that owns the model.

    MLX keeps its GPU stream on the thread that created it. The web server
    runs each request on a pool thread, so a later line would evaluate the
    model where that stream does not exist.
    """

    global _MODEL_QUEUE, _MODEL_THREAD
    with _MODEL_GATE:
        if _MODEL_THREAD is None or not _MODEL_THREAD.is_alive():
            _MODEL_QUEUE = queue.Queue()
            _MODEL_THREAD = threading.Thread(
                target=_model_loop,
                args=(_MODEL_QUEUE,),
                name="skit-model",
                daemon=True,
            )
            _MODEL_THREAD.start()
        jobs = _MODEL_QUEUE
    done: queue.Queue = queue.Queue(1)
    jobs.put((work, done))
    ok, result = done.get()
    if not ok:
        raise result
    return result


def _engine():
    global _ENGINE, _ENGINE_NAME
    name = engine_name()
    if _ENGINE is None or _ENGINE_NAME != name:
        if _ENGINE_NAME == "f5":
            from app.engines import f5_backend

            if f5_backend() == "mlx":
                from app.engines.f5 import unload
            else:
                from app.engines.f5_torch import unload

            unload()
        elif _ENGINE_NAME == "chatterbox":
            from app.engines.chatterbox import unload

            unload()
        _ENGINE = build(name)
        _ENGINE_NAME = name
    return _ENGINE


def normalized_clip(clip: ResolvedClip) -> Path:
    dest = data_dir() / "clips" / clip.speaker / f"{clip.used_emotion}-{clip.fingerprint}.wav"
    if not dest.exists():
        to_wav_24k_mono(clip.path, dest)
    return dest


# Bump when synthesis post-processing changes, so older cached wavs are not reused.
CACHE_REVISION = "neutral-span-2"


def cache_revision() -> str:
    """Mac keeps neutral-span-2. Windows drops takes made with euler or the chunk crossfade."""

    revision = CACHE_REVISION
    if engine_name() == "f5":
        from app.engines import f5_backend

        if f5_backend() == "torch":
            # Euler at 8 steps left a noise bed on the second line. rk4 matches the Mac.
            revision = f"{revision}+torch-rk4"
    return revision


def cache_key(clip: ResolvedClip, text: str, emotion: str) -> str:
    digest = hashlib.sha256()
    digest.update(cache_revision().encode())
    digest.update(b"\0")
    digest.update(engine_name().encode())
    digest.update(b"\0")
    digest.update(clip.speaker.encode())
    digest.update(b"\0")
    digest.update(emotion.encode())
    digest.update(b"\0")
    digest.update(text.strip().encode())
    digest.update(b"\0")
    digest.update(clip.fingerprint.encode())
    return digest.hexdigest()[:24]


def synthesize(clip: ResolvedClip, text: str, emotion: str) -> tuple[Path, str]:
    """Return the wav path and the cache key. Raises BusyError if a line is already running."""

    key = cache_key(clip, text, emotion)
    dest = data_dir() / "cache" / f"{key}.wav"
    if dest.exists() and dest.stat().st_size > 44:
        return dest, key
    if not _LOCK.acquire(blocking=False):
        raise BusyError("Already generating a line. Wait for it to finish.")
    try:
        if dest.exists() and dest.stat().st_size > 44:
            return dest, key
        ref = normalized_clip(clip)
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(".part.wav")
        start_progress(clip.speaker)

        def _speak() -> None:
            _engine().synthesize(
                text=text.strip(),
                ref_wav=ref,
                ref_text=clip.transcript,
                emotion=emotion,
                out_path=partial,
            )
            progress_phase("saving")
            target = speaker_output_rms(clip.speaker)
            if target is not None:
                raise_to_rms(partial, target)
            partial.replace(dest)

        try:
            _on_model_thread(_speak)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        finally:
            clear_progress()
        return dest, key
    finally:
        _LOCK.release()


def busy() -> bool:
    locked = _LOCK.acquire(blocking=False)
    if locked:
        _LOCK.release()
        return False
    return True
