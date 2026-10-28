"""Reference clips for L and Kira. Emotion falls back to the neutral clip."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.config import EMOTIONS, SPEAKERS, voices_dir

AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aiff", ".aif"}


@dataclass(frozen=True)
class ResolvedClip:
    speaker: str
    requested_emotion: str
    used_emotion: str
    path: Path
    transcript: str
    fell_back: bool

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.path.read_bytes())
        digest.update(b"\0")
        digest.update(self.transcript.encode("utf-8"))
        return digest.hexdigest()[:16]


def clip_path(speaker: str, emotion: str) -> Path | None:
    folder = voices_dir() / speaker
    if not folder.is_dir():
        return None
    for suffix in AUDIO_SUFFIXES:
        candidate = folder / f"{emotion}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def transcript_for(path: Path) -> str:
    sidecar = path.with_suffix(".txt")
    if not sidecar.is_file():
        return ""
    return " ".join(sidecar.read_text(encoding="utf-8").split())


def resolve(speaker: str, emotion: str) -> ResolvedClip:
    if speaker not in SPEAKERS:
        raise ValueError(f"Unknown speaker '{speaker}'.")
    if emotion in EMOTIONS:
        direct = clip_path(speaker, emotion)
        if direct is not None:
            return ResolvedClip(
                speaker=speaker,
                requested_emotion=emotion,
                used_emotion=emotion,
                path=direct,
                transcript=transcript_for(direct),
                fell_back=False,
            )
    if emotion != "neutral":
        neutral = clip_path(speaker, "neutral")
        if neutral is not None:
            return ResolvedClip(
                speaker=speaker,
                requested_emotion=emotion,
                used_emotion="neutral",
                path=neutral,
                transcript=transcript_for(neutral),
                fell_back=True,
            )
    raise FileNotFoundError(
        f"Add voices/{speaker}/neutral.wav (and neutral.txt for F5) before generating."
    )


def bank_status() -> dict:
    speakers = {}
    for speaker in SPEAKERS:
        emotions = {}
        for emotion in EMOTIONS:
            path = clip_path(speaker, emotion)
            emotions[emotion] = {
                "present": path is not None,
                "file": path.name if path else None,
                "transcript": bool(path and transcript_for(path)),
            }
        speakers[speaker] = emotions
    return {"speakers": speakers, "emotions": list(EMOTIONS)}
