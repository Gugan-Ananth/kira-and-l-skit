"""Paths and runtime settings. Read from the environment on each call."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EMOTIONS = ("neutral",)
SPEAKERS = ("l", "kira")
SPEAKER_LABELS = {"l": "L", "kira": "Kira"}
ENGINES = ("f5", "chatterbox", "fake")

# Whole-file loudness after synthesis. L's clip is quiet, so the clone is quiet.
SPEAKER_OUTPUT_RMS = {"l": 0.05}

# Chatterbox exaggeration, kept inside the 0–1 range the MLX port documents.
# Delivery still follows the reference clip. These values only push intensity.
CHATTERBOX_EXAGGERATION = {
    "neutral": 0.25,
    "calm": 0.15,
    "tense": 0.55,
    "mocking": 0.65,
    "angry": 0.90,
    "whisper": 0.12,
    "sad": 0.35,
}

# Spoken only on a Turbo checkpoint. The 4-bit English model reads these aloud.
CHATTERBOX_TURBO_TAGS = {
    "neutral": "",
    "calm": "",
    "tense": "",
    "mocking": "[chuckle] ",
    "angry": "",
    "whisper": "",
    "sad": "[sigh] ",
}


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


_load_dotenv()


def data_dir() -> Path:
    return Path(os.environ.get("SKIT_DATA", ROOT / "data"))


def voices_dir() -> Path:
    return Path(os.environ.get("SKIT_VOICES", ROOT / "voices"))


def host() -> str:
    return os.environ.get("SKIT_HOST", "127.0.0.1")


def port() -> int:
    return int(os.environ.get("SKIT_PORT", "8765"))


def password() -> str:
    return os.environ.get("SKIT_PASSWORD", "")


def engine_name() -> str:
    name = os.environ.get("SKIT_ENGINE", "f5").strip().lower()
    if name not in ENGINES:
        raise ValueError(f"SKIT_ENGINE must be one of {', '.join(ENGINES)}")
    return name


def exposed() -> bool:
    return host() not in {"127.0.0.1", "localhost", "::1"}


def f5_steps() -> int:
    return int(os.environ.get("SKIT_F5_STEPS", "8"))


def f5_method() -> str:
    method = os.environ.get("SKIT_F5_METHOD", "rk4")
    if method not in {"euler", "midpoint", "rk4"}:
        raise ValueError("SKIT_F5_METHOD must be euler, midpoint, or rk4")
    return method


def f5_model_path() -> Path:
    raw = os.environ.get("SKIT_F5_MODEL", "")
    if raw:
        return Path(raw)
    return ROOT / "models" / "f5" / "model_1250000.safetensors"


def vocos_path() -> Path:
    raw = os.environ.get("SKIT_VOCOS", "")
    if raw:
        return Path(raw)
    return ROOT / "models" / "vocos"


def f5_device() -> str:
    return os.environ.get("SKIT_F5_DEVICE", "cpu")


def chatterbox_model() -> str:
    return os.environ.get("SKIT_CHATTERBOX_MODEL", "mlx-community/chatterbox-4bit")


def speaker_output_rms(speaker: str) -> float | None:
    return SPEAKER_OUTPUT_RMS.get(speaker)
