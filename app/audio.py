"""ffmpeg helpers. The speech model wants mono 24 kHz wav."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


class AudioError(RuntimeError):
    pass


def ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise AudioError("ffmpeg is not installed.")
    return found


def run_ffmpeg(args: list[str]) -> None:
    try:
        subprocess.run(
            [ffmpeg(), "-hide_banner", "-loglevel", "error", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise AudioError(detail or "ffmpeg failed") from exc


def to_wav_24k_mono(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )


def silence_wav(dest: Path, milliseconds: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    seconds = max(0, milliseconds) / 1000
    if seconds == 0:
        run_ffmpeg(
            [
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-t",
                "0.001",
                "-c:a",
                "pcm_s16le",
                str(dest),
            ]
        )
        return
    run_ffmpeg(
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono",
            "-t",
            f"{seconds:.3f}",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )


def raise_to_rms(path: Path, target_rms: float, ceiling: float = 0.9) -> None:
    """Lift a quiet wav up to target_rms. Leaves a louder file alone and avoids clipping."""

    import numpy as np
    import soundfile as sf

    audio, sample_rate = sf.read(path)
    if getattr(audio, "ndim", 1) > 1:
        audio = np.mean(audio, axis=1)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0 or target_rms <= 0:
        return
    rms = float(np.sqrt(np.mean(np.square(audio))))
    if rms < 1e-5 or rms >= target_rms:
        return
    scaled = audio * (target_rms / rms)
    peak = float(np.max(np.abs(scaled)))
    if peak > ceiling:
        scaled *= ceiling / peak
    sf.write(path, scaled, sample_rate)


# Sample peak of an MP3 download. A wav at 0.98 still reconstructs over
# full scale in LAME on a bright syllable. This is attenuation only.
MP3_PEAK_DB = -1.0
MP3_PEAK = 10 ** (MP3_PEAK_DB / 20.0)
# Bump when that ceiling changes, so an already saved MP3 is encoded again.
MP3_EXPORT_REVISION = "peak-1db"

# id, file extension, ffmpeg codec args, media type, encoder name in `ffmpeg -encoders`
DOWNLOAD_FORMATS = (
    ("mp3", "mp3", ("-c:a", "libmp3lame", "-q:a", "2"), "audio/mpeg", "libmp3lame"),
    ("wav", "wav", ("-c:a", "pcm_s16le"), "audio/wav", "pcm_s16le"),
    ("flac", "flac", ("-c:a", "flac"), "audio/flac", "flac"),
    ("m4a", "m4a", ("-c:a", "aac", "-b:a", "192k"), "audio/mp4", "aac"),
    ("ogg", "ogg", ("-c:a", "libvorbis", "-q:a", "5"), "audio/ogg", "libvorbis"),
)

_ready_formats: list[dict] | None = None


def ready_download_formats() -> list[dict]:
    """Formats this ffmpeg build can actually write."""

    global _ready_formats
    if _ready_formats is not None:
        return _ready_formats
    try:
        result = subprocess.run(
            [ffmpeg(), "-hide_banner", "-encoders"],
            check=False,
            capture_output=True,
            text=True,
        )
        listed = result.stdout
    except AudioError:
        listed = ""
    ready = []
    for key, ext, args, media, encoder in DOWNLOAD_FORMATS:
        if encoder == "pcm_s16le" or re.search(rf"\b{re.escape(encoder)}\b", listed):
            ready.append({"id": key, "ext": ext, "args": args, "media": media})
    _ready_formats = ready
    return ready


def peak_limited_wav(source: Path, dest: Path, ceiling: float = MP3_PEAK) -> Path:
    """Return a wav whose sample peak is at most ceiling.

    Returns source when the file is already quieter, so a soft line is not lifted.
    """

    import numpy as np
    import soundfile as sf

    audio, sample_rate = sf.read(source)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return source
    peak = float(np.max(np.abs(audio)))
    if peak <= ceiling or peak < 1e-8:
        return source
    dest.parent.mkdir(parents=True, exist_ok=True)
    sf.write(
        dest,
        np.ascontiguousarray(audio * (ceiling / peak)),
        sample_rate,
        subtype="PCM_16",
    )
    return dest


def convert_download(source: Path, dest: Path, fmt: str) -> str:
    """Write source as fmt. Returns the media type. Raises AudioError."""

    match = next((item for item in ready_download_formats() if item["id"] == fmt), None)
    if match is None:
        names = ", ".join(item["id"] for item in ready_download_formats()) or "wav"
        raise AudioError(f"Choose {names}.")
    mux = {"mp3": "mp3", "wav": "wav", "flac": "flac", "m4a": "ipod", "ogg": "ogg"}[fmt]
    dest.parent.mkdir(parents=True, exist_ok=True)
    encode_from = source
    leveled = dest.with_name(dest.name + ".level.wav")
    if fmt == "mp3":
        encode_from = peak_limited_wav(source, leveled)
    try:
        run_ffmpeg(["-y", "-i", str(encode_from), *match["args"], "-f", mux, str(dest)])
    finally:
        if encode_from != source:
            # The encode has already finished. A locked temp file must not
            # fail the download on Windows.
            try:
                leveled.unlink(missing_ok=True)
            except OSError:
                pass
    return match["media"]


def concat_wavs(parts: list[Path], dest: Path) -> None:
    if not parts:
        raise AudioError("Nothing to export.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    listing = dest.with_suffix(".txt")
    lines = []
    for part in parts:
        escaped = str(part.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        run_ffmpeg(
            [
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c:a",
                "pcm_s16le",
                str(dest),
            ]
        )
    finally:
        listing.unlink(missing_ok=True)
