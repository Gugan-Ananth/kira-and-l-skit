"""Reference fitting shared by the Mac and Windows F5 engines. Numpy only."""

from __future__ import annotations

import re

import numpy as np

SAMPLE_RATE = 24_000
# F5 degrades past this length and starts mixing the reference back in.
MAX_REF_SECONDS = 12.0
# A timed recording is cut to one span before the edge pad, so the pad still
# fits under MAX_REF_SECONDS. Cutting words off an aligned span makes F5 speak
# the missing words before the requested line.
SPAN_MAX_SECONDS = 11.2
# Spoken span F5 can copy. Shorter than this and the voice wanders. Faster or
# slower than this is a shout, a pause-heavy take, or a transcript that does
# not match the audio.
MIN_REF_SECONDS = 5.0
MIN_REF_WORDS = 8
MIN_REF_PACE = 2.0
MAX_REF_PACE = 4.2
# Breaths between cues in these recordings. A wider gap is a new delivery.
MAX_CUE_GAP = 0.5
# Same level the sampler uses, so a quiet clip still conditions the voice.
TARGET_RMS = 0.1
# Words faster than this are not in the clip. Leaving them in the transcript
# makes F5 say them before the requested line. A fast dub is about this pace.
PLAUSIBLE_WORDS_PER_SECOND = 4.5
# Above this, the file is a short excerpt with the whole line written beside it.
RUNAWAY_WORDS_PER_SECOND = 6.0
EXCERPT_WORDS_PER_SECOND = 3.8
# A clip that ends on a cut syllable needs a pause after it. Otherwise F5
# continues that syllable and the same cut removes the start of the next line.
TRAIL_PAD_SECONDS = 0.25


def split_sentences(text: str) -> list[str]:
    pieces = re.split(r"(?<=[.!?])\s+", text.strip())
    return [piece.strip() for piece in pieces if piece.strip()] or [text.strip()]


_CUE_RE = re.compile(
    r"\[(\d{1,2}):(\d{2}(?:\.\d+)?)\s*-\s*(\d{1,2}):(\d{2}(?:\.\d+)?)\]\s*"
    r"(.*?)(?=\s*\[\d{1,2}:\d{2}(?:\.\d+)?\s*-|\Z)",
    re.DOTALL,
)


def _clock(minutes: str, seconds: str) -> float:
    return int(minutes) * 60 + float(seconds)


def parse_cues(transcript: str) -> list[tuple[float, float, str]]:
    """Timed lines in a sidecar. Empty when the sidecar is just the spoken words."""

    cues = []
    for match in _CUE_RE.finditer(transcript):
        start = _clock(match.group(1), match.group(2))
        end = _clock(match.group(3), match.group(4))
        text = " ".join(match.group(5).split())
        if text and end > start:
            cues.append((start, end, text))
    return cues


def _neutral_span(text: str) -> bool:
    words = text.split()
    shouted = sum(1 for word in words if len(word) > 2 and word.isupper())
    return shouted < 2 and "!" not in text


def _span_score(duration: float, text: str) -> float | None:
    words = text.split()
    if duration < MIN_REF_SECONDS or duration > SPAN_MAX_SECONDS:
        return None
    if len(words) < MIN_REF_WORDS:
        return None
    pace = len(words) / duration
    if pace < MIN_REF_PACE or pace > MAX_REF_PACE:
        return None
    if not _neutral_span(text):
        return None
    # Ten seconds around a normal dub pace is the clip F5 matches most closely.
    duration_score = 1.0 - abs(duration - 9.5) / 5.0
    pace_score = 1.0 - abs(pace - 3.1) / 2.0
    ending = 0.2 if text[-1] in ".?" else 0.0
    return duration_score + 0.35 * pace_score + ending


def _cue_ok(start: float, end: float, text: str) -> bool:
    """A line can join the reference only when it is already an even take."""

    duration = end - start
    if duration < 2.5 or not _neutral_span(text):
        return False
    pace = len(text.split()) / duration
    return MIN_REF_PACE <= pace <= MAX_REF_PACE


def choose_span(
    cues: list[tuple[float, float, str]],
) -> tuple[float, float, str] | None:
    """One even span. Shouts, rushes, and pause-heavy takes are left out."""

    best: tuple[float, float, str] | None = None
    best_score = -1.0
    for start_at, first in enumerate(cues):
        parts: list[str] = []
        previous_end: float | None = None
        for cue in cues[start_at:]:
            if not _cue_ok(*cue):
                break
            if previous_end is not None and cue[0] - previous_end > MAX_CUE_GAP:
                break
            parts.append(cue[2])
            previous_end = cue[1]
            duration = cue[1] - first[0]
            if duration > SPAN_MAX_SECONDS:
                break
            text = " ".join(parts)
            score = _span_score(duration, text)
            if score is not None and score > best_score:
                best_score = score
                best = (first[0], cue[1], text)
    return best


def _slice_span(
    audio: np.ndarray,
    sample_rate: int,
    cues: list[tuple[float, float, str]],
    span: tuple[float, float, str],
) -> np.ndarray | None:
    """The span, plus a short edge that stops before the next line."""

    previous_end = 0.0
    next_start = audio.shape[0] / sample_rate
    for start, end, _text in cues:
        if end <= span[0] + 1e-3:
            previous_end = max(previous_end, end)
        elif start >= span[1] - 1e-3:
            next_start = min(next_start, start)
    begin = max(0.0, max(span[0] - 0.06, previous_end))
    finish = min(span[1] + 0.18, next_start)
    start_at = int(begin * sample_rate)
    end_at = min(audio.shape[0], int(finish * sample_rate))
    if end_at - start_at < int(sample_rate * 0.3):
        return None
    return np.ascontiguousarray(audio[start_at:end_at])


def match_reference_level(audio: np.ndarray) -> np.ndarray:
    """Lift a quiet reference to the level F5 was sampled with. Leave a loud one."""

    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    if rms < 1e-5 or rms >= TARGET_RMS:
        return audio
    scaled = np.asarray(audio * (TARGET_RMS / rms), dtype=np.float32)
    peak = float(np.max(np.abs(scaled)))
    if peak > 0.99:
        scaled = scaled * (0.99 / peak)
    return np.ascontiguousarray(scaled)


def trim_edge_silence(
    audio: np.ndarray,
    sample_rate: int,
    *,
    keep_ms: int = 120,
    relative_db: float = -32.0,
) -> np.ndarray:
    """Drop silence at the ends. Threshold is relative to the peak, so a quiet voice stays."""

    if audio.size < sample_rate // 5:
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak < 1e-5:
        return audio
    threshold = peak * 10 ** (relative_db / 20.0)
    frame = max(1, int(sample_rate * 0.02))
    first = None
    last = None
    for start in range(0, audio.shape[0] - frame + 1, frame):
        if float(np.max(np.abs(audio[start : start + frame]))) >= threshold:
            if first is None:
                first = start
            last = start
    if first is None or last is None:
        return audio
    pad = int(sample_rate * keep_ms / 1000)
    begin = max(0, first - pad)
    end = min(audio.shape[0], last + frame + pad)
    if end - begin < int(sample_rate * 0.3):
        return audio
    return np.ascontiguousarray(audio[begin:end])


def _ends_in_speech(audio: np.ndarray, sample_rate: int) -> bool:
    tail_n = max(1, int(sample_rate * 0.05))
    if audio.shape[0] < tail_n:
        return False
    peak = float(np.max(np.abs(audio)))
    if peak < 1e-5:
        return False
    threshold = peak * 10 ** (-32.0 / 20.0)
    return float(np.max(np.abs(audio[-tail_n:]))) >= threshold


def _close_reference(
    audio: np.ndarray,
    sample_rate: int,
    text: str,
) -> tuple[np.ndarray, str]:
    """Finish the clip on a sentence and, if it was cut mid-syllable, a pause."""

    cleaned = text.strip()
    if cleaned and cleaned[-1] not in ".!?":
        cleaned += "."
    if not _ends_in_speech(audio, sample_rate):
        return audio, cleaned
    closed = np.array(audio, dtype=np.float32, copy=True)
    fade_n = int(sample_rate * 0.02)
    if closed.shape[0] > fade_n > 0:
        closed[-fade_n:] *= np.linspace(1.0, 0.0, fade_n, dtype=np.float32)
    pad = np.zeros(int(sample_rate * TRAIL_PAD_SECONDS), dtype=np.float32)
    return np.concatenate([closed, pad]), cleaned


def prepare_reference(
    audio: np.ndarray,
    sample_rate: int,
    transcript: str,
) -> tuple[np.ndarray, str]:
    """Keep a reference F5 can align: short audio, and only the words spoken in it."""

    cues = parse_cues(transcript)
    span = choose_span(cues) if cues else None
    if span is not None:
        sliced = _slice_span(audio, sample_rate, cues, span)
        if sliced is not None:
            audio = sliced
            transcript = span[2]
    trimmed = trim_edge_silence(audio, sample_rate)
    words = transcript.split()
    max_samples = int(MAX_REF_SECONDS * sample_rate)
    if trimmed.shape[0] > max_samples and words:
        ratio = max_samples / trimmed.shape[0]
        trimmed = np.ascontiguousarray(trimmed[:max_samples])
        words = words[: max(1, int(round(len(words) * ratio)))]
    seconds = max(trimmed.shape[0] / sample_rate, 0.1)
    pace = len(words) / seconds if words else 0.0
    if pace > PLAUSIBLE_WORDS_PER_SECOND:
        fit = EXCERPT_WORDS_PER_SECOND if pace > RUNAWAY_WORDS_PER_SECOND else PLAUSIBLE_WORDS_PER_SECOND
        words = words[: max(1, int(seconds * fit))]
    return _close_reference(trimmed, sample_rate, " ".join(words))


def generation_frames(ref_samples: int, ref_text: str, gen_text: str) -> int:
    """Frames for the reference plus the new line, at the reference speaker's pace."""

    hop = 256
    ref_frames = max(1, ref_samples // hop)
    ref_bytes = max(1, len(ref_text.encode("utf-8")))
    gen_bytes = max(1, len(gen_text.encode("utf-8")))
    return ref_frames + max(1, int(ref_frames * gen_bytes / ref_bytes))


def polish_output(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Trim the dead air a long duration leaves, and pull a hard clip under full scale."""

    joined = trim_edge_silence(audio, sample_rate, keep_ms=120)
    peak = float(np.max(np.abs(joined))) if joined.size else 0.0
    if peak > 0.98:
        joined = np.ascontiguousarray(joined * (0.98 / peak))
    return joined
