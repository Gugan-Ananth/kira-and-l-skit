import wave
from pathlib import Path

import numpy as np
import pytest

from app.audio import MP3_PEAK, convert_download, peak_limited_wav, raise_to_rms
from app.reference import (
    EXCERPT_WORDS_PER_SECOND,
    TRAIL_PAD_SECONDS,
    generation_frames,
    prepare_reference,
    trim_edge_silence,
)


def test_overlong_transcript_keeps_only_the_words_that_fit():
    rate = 24_000
    audio = np.zeros(int(rate * 4.2), dtype=np.float32)
    audio[int(rate * 0.9) : int(rate * 3.6)] = 0.2
    transcript = " ".join(f"word{i}" for i in range(48))
    fitted, text = prepare_reference(audio, rate, transcript)
    words = text.split()
    assert fitted.shape[0] < audio.shape[0]
    assert 8 <= len(words) <= 14
    assert words[0] == "word0"
    assert "word40" not in words


def test_matching_transcript_is_kept():
    rate = 24_000
    audio = np.full(int(rate * 10), 0.05, dtype=np.float32)
    transcript = " ".join(f"word{i}" for i in range(45))
    fitted, text = prepare_reference(audio, rate, transcript)
    assert [word.strip(".") for word in text.split()] == transcript.split()
    # The tone runs to the last sample, so the clip gains a pause after it.
    assert fitted.shape[0] == audio.shape[0] + int(rate * TRAIL_PAD_SECONDS)
    tail = fitted[-int(rate * 0.1) :]
    assert float(np.max(np.abs(tail))) < 1e-4


def test_long_clip_is_capped_and_the_transcript_follows():
    rate = 24_000
    audio = np.full(int(rate * 40), 0.05, dtype=np.float32)
    transcript = " ".join(f"word{i}" for i in range(80))
    fitted, text = prepare_reference(audio, rate, transcript)
    assert fitted.shape[0] == int(rate * 12) + int(rate * TRAIL_PAD_SECONDS)
    kept = len(text.split())
    assert 20 <= kept <= 28
    assert text.startswith("word0")


def test_excerpt_transcript_keeps_a_normal_pace_and_ends_on_a_pause():
    # A few seconds of audio with the whole monologue written beside it.
    # A fast fit speaks words that are not in the clip. A slow fit leaves
    # speech in the tail, and F5 cuts the start of the next line.
    rate = 24_000
    seconds = 3.73
    audio = np.full(int(rate * seconds), 0.05, dtype=np.float32)
    transcript = " ".join(f"word{i}" for i in range(48))
    fitted, text = prepare_reference(audio, rate, transcript)
    words = [word.strip(".") for word in text.split()]
    assert words[0] == "word0"
    assert len(words) == int(seconds * EXCERPT_WORDS_PER_SECOND)
    assert text.endswith(".")
    tail = fitted[-int(rate * 0.12) :]
    assert float(np.max(np.abs(tail))) < 1e-4


def test_duration_follows_the_reference_pace():
    frames = generation_frames(24_000 * 10, "a" * 100, "b" * 50)
    ref_frames = (24_000 * 10) // 256
    assert frames == ref_frames + int(ref_frames * 50 / 100)


def test_timed_transcript_uses_one_neutral_span():
    rate = 24_000
    audio = np.zeros(rate * 30, dtype=np.float32)
    audio[int(rate * 10.2) : int(rate * 16.3)] = 0.15
    raw = (
        "Transcript: nobody\n"
        "[00:00.00 - 00:09.50] I know exactly what you did last night and I am "
        "going to say every part of it again right now so you cannot hide!\n"
        "[00:10.20 - 00:16.40] I checked the names and the times on the paper. "
        "Nothing else is missing today.\n"
    )
    fitted, text = prepare_reference(audio, rate, " ".join(raw.split()))
    assert text.startswith("I checked the names")
    assert "hide" not in text
    assert "Transcript" not in text
    assert "[" not in text
    assert float(np.sqrt(np.mean(np.square(fitted[: int(rate * 0.4)])))) > 0.02


def test_trim_drops_edge_silence_and_keeps_a_quiet_voice():
    rate = 24_000
    audio = np.zeros(rate * 3, dtype=np.float32)
    audio[rate : rate * 2] = 0.02
    trimmed = trim_edge_silence(audio, rate, keep_ms=100)
    assert trimmed.shape[0] < audio.shape[0]
    assert trimmed.shape[0] > rate // 2


def _tone(path: Path, amplitude: int) -> None:
    rate = 24_000
    frames = rate
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        pcm = bytearray()
        for index in range(frames):
            sample = int(amplitude * np.sin(2 * np.pi * 220 * index / rate))
            pcm += int(sample).to_bytes(2, "little", signed=True)
        handle.writeframes(bytes(pcm))


def test_mp3_export_caps_a_hot_file_and_leaves_a_quiet_one(tmp_path):
    import soundfile as sf

    from app.audio import run_ffmpeg

    hot = tmp_path / "hot.wav"
    quiet = tmp_path / "quiet.wav"
    _tone(hot, 32767)
    _tone(quiet, 800)
    hot_mp3 = tmp_path / "hot.mp3"
    quiet_mp3 = tmp_path / "quiet.mp3"
    convert_download(hot, hot_mp3, "mp3")
    convert_download(quiet, quiet_mp3, "mp3")

    def decoded_peak(path: Path) -> float:
        wav = path.with_suffix(".decoded.wav")
        run_ffmpeg(["-y", "-i", str(path), "-c:a", "pcm_f32le", str(wav)])
        audio, _rate = sf.read(wav)
        return float(np.max(np.abs(audio)))

    # LAME can sit about 0.01 dB over the wav it was given. An uncapped
    # full-scale tone comes back near 0 dBFS, so -0.9 dB still proves the cap.
    assert decoded_peak(hot_mp3) <= 10 ** (-0.9 / 20.0)
    assert decoded_peak(quiet_mp3) < 0.05

    leveled = tmp_path / "leveled.wav"
    written = peak_limited_wav(hot, leveled)
    leveled_audio, _rate = sf.read(written)
    # PCM_16 stores -1 dBFS as the nearest integer step, one count over the float.
    assert float(np.max(np.abs(leveled_audio))) <= MP3_PEAK + (1 / 32767)
    assert peak_limited_wav(quiet, tmp_path / "quiet-level.wav") == quiet
    assert not (tmp_path / "quiet-level.wav").exists()


def test_raise_to_rms_lifts_a_quiet_file_and_leaves_a_loud_one(tmp_path):
    import soundfile as sf

    quiet = tmp_path / "quiet.wav"
    loud = tmp_path / "loud.wav"
    _tone(quiet, 400)
    _tone(loud, 12000)
    before_loud, _ = sf.read(loud)
    raise_to_rms(quiet, 0.05)
    raise_to_rms(loud, 0.05)
    quiet_audio, _ = sf.read(quiet)
    loud_audio, _ = sf.read(loud)
    quiet_rms = float(np.sqrt(np.mean(np.square(quiet_audio))))
    assert quiet_rms == pytest.approx(0.05, abs=0.005)
    assert np.allclose(loud_audio, before_loud)
