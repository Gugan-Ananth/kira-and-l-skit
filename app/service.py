"""Skit actions shared by the API."""

from __future__ import annotations

import hashlib
import re
import shutil
import traceback
from pathlib import Path

from app.audio import (
    MP3_EXPORT_REVISION,
    AudioError,
    concat_wavs,
    convert_download,
    ready_download_formats,
    silence_wav,
    to_wav_24k_mono,
)
from app.config import EMOTIONS, SPEAKERS, data_dir
from app.db import get_line, get_skit, update_line
from app.engines import EngineError
from app.synth import BusyError, synthesize
from app.voices import resolve

TEXT_LIMIT = 2000


class SkitError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def check_speaker(speaker: str) -> str:
    if speaker not in SPEAKERS:
        raise SkitError("Speaker must be L or Kira.")
    return speaker


def check_emotion(emotion: str) -> str:
    if emotion not in EMOTIONS:
        raise SkitError("Unknown emotion.")
    return emotion


def check_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise SkitError("Write the line first.")
    if len(cleaned) > TEXT_LIMIT:
        raise SkitError(f"A line can be at most {TEXT_LIMIT} characters.")
    return cleaned


def screen_message(exc: BaseException) -> str:
    """The page shows one short reason. The log keeps the full traceback."""

    raw = str(exc).strip()
    if "libtorchcodec" in raw.lower():
        return "Could not load the speech decoder. FFmpeg's shared libraries are missing."
    text = raw or type(exc).__name__
    name = type(exc).__name__
    if not text.startswith(name):
        text = f"{name}: {text}"
    for marker in (
        "Traceback (most recent call last):",
        "[start of libtorchcodec",
        "The following exceptions were raised",
    ):
        index = text.find(marker)
        if index >= 0:
            text = text[:index]
    text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(text) > 320:
        text = text[:320].rstrip() + "…"
    return text or "The speech engine failed."


def _remember_failure(line_id: str, exc: Exception, message: str) -> None:
    try:
        folder = data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "desktop.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{type(exc).__name__}: {exc}\n" + traceback.format_exc() + "\n")
    except OSError:
        pass
    update_line(line_id, {"status": "error", "error": message})


def check_pause(pause_ms: int) -> int:
    if not isinstance(pause_ms, int) or pause_ms < 0 or pause_ms > 10000:
        raise SkitError("Pause must be between 0 and 10000 milliseconds.")
    return pause_ms


def generate_one(line_id: str) -> dict:
    line = get_line(line_id)
    if line is None:
        raise SkitError("Line not found.", 404)
    if line["mode"] == "saved":
        raise SkitError("This line uses a saved take. Switch it back to speech to generate.")
    text = check_text(line["text"])
    try:
        clip = resolve(line["speaker"], line["emotion"])
        path, key = synthesize(clip, text, line["emotion"])
    except BusyError as exc:
        raise SkitError(str(exc), 409) from exc
    except (EngineError, FileNotFoundError, ValueError, AudioError) as exc:
        message = screen_message(exc)
        _remember_failure(line_id, exc, message)
        raise SkitError(message, 400) from exc
    except Exception as exc:
        message = screen_message(exc)
        _remember_failure(line_id, exc, message)
        raise SkitError(message, 500) from exc
    return update_line(
        line_id,
        {
            "audio_path": str(path),
            "audio_key": key,
            "status": "fallback" if clip.fell_back else "ready",
            "error": None,
            "used_emotion": clip.used_emotion,
        },
    )


def generate_skit(skit_id: str) -> dict:
    skit = get_skit(skit_id)
    if skit is None:
        raise SkitError("Skit not found.", 404)
    for line in skit["lines"]:
        if line["mode"] == "saved":
            continue
        if line["status"] in {"ready", "fallback"}:
            continue
        if not line["text"].strip():
            continue
        try:
            generate_one(line["id"])
        except SkitError as exc:
            if exc.status == 409:
                raise
    return get_skit(skit_id)


def playback_path(line: dict) -> Path | None:
    if line["mode"] == "saved" and line.get("saved_path"):
        path = Path(line["saved_path"])
        return path if path.is_file() else None
    if line.get("audio_path"):
        path = Path(line["audio_path"])
        return path if path.is_file() else None
    return None


def export_wav(skit_id: str) -> Path:
    skit = get_skit(skit_id)
    if skit is None:
        raise SkitError("Skit not found.", 404)
    ready = []
    for line in skit["lines"]:
        path = playback_path(line)
        if path is None:
            raise SkitError("Generate every spoken line before exporting.")
        if line["mode"] != "saved" and line["status"] == "stale":
            raise SkitError("A line changed after it was generated. Generate it again, then export.")
        ready.append((line, path))
    if not ready:
        raise SkitError("Add a line before exporting.")

    work = data_dir() / "exports" / skit_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    parts: list[Path] = []
    for index, (line, source) in enumerate(ready):
        spoken = work / f"{index:03d}.wav"
        to_wav_24k_mono(source, spoken)
        parts.append(spoken)
        if index < len(ready) - 1:
            gap = work / f"{index:03d}-gap.wav"
            silence_wav(gap, int(line["pause_ms"]))
            parts.append(gap)
    dest = work / "skit.wav"
    concat_wavs(parts, dest)
    return dest


def prepared_download(source: Path, fmt: str) -> tuple[Path, str]:
    """Convert source into the requested format and return the file plus media type."""

    known = {item["id"]: item for item in ready_download_formats()}
    item = known.get(fmt)
    if item is None:
        names = ", ".join(known) or "wav"
        raise SkitError(f"Choose {names}.")
    stat = source.stat()
    # The revision is part of the name so a PC that already saved this line
    # encodes the MP3 again instead of copying the hot file.
    revision = MP3_EXPORT_REVISION if fmt == "mp3" else ""
    digest = hashlib.sha256(
        f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{fmt}|{revision}".encode()
    ).hexdigest()[:24]
    final = data_dir() / "downloads" / f"{digest}.{item['ext']}"
    if final.is_file() and final.stat().st_size > 32:
        return final, item["media"]
    partial = final.with_suffix(".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    try:
        media = convert_download(source, partial, fmt)
    except AudioError as exc:
        partial.unlink(missing_ok=True)
        raise SkitError(str(exc) or "Could not convert that audio.") from exc
    partial.replace(final)
    return final, media


def line_download(line_id: str, fmt: str) -> tuple[Path, str, str]:
    line = get_line(line_id)
    if line is None:
        raise SkitError("Line not found.", 404)
    source = playback_path(line)
    if source is None:
        raise SkitError("This line has no audio yet.", 404)
    path, media = prepared_download(source, fmt)
    skit = get_skit(line["skit_id"])
    index = 1
    if skit is not None:
        for position, item in enumerate(skit["lines"], start=1):
            if item["id"] == line_id:
                index = position
                break
    return path, media, f"{line['speaker']}-{index}.{fmt}"


def skit_download(skit_id: str, fmt: str) -> tuple[Path, str, str]:
    path, media = prepared_download(export_wav(skit_id), fmt)
    skit = get_skit(skit_id)
    title = safe_title(skit["title"]) if skit else "skit"
    return path, media, f"{title}.{fmt}"


def safe_title(title: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-")
    return cleaned or "skit"


def store_saved_take(line_id: str, source: Path) -> dict:
    line = get_line(line_id)
    if line is None:
        raise SkitError("Line not found.", 404)
    dest = data_dir() / "saved" / line["skit_id"] / f"{line_id}.wav"
    try:
        to_wav_24k_mono(source, dest)
    except AudioError as exc:
        raise SkitError(f"Could not read that audio file. {exc}") from exc
    return update_line(
        line_id,
        {
            "mode": "saved",
            "saved_path": str(dest),
            "status": "ready",
            "error": None,
            "used_emotion": None,
        },
    )
