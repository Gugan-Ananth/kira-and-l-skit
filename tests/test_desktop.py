import os
from pathlib import Path

import numpy as np
import pytest

from app.config import ROOT
from app.desktop import DesktopApi, apply_offline, parse_download_url, save_to_downloads
from app.engines import EngineError, build, f5_backend
from app.engines.f5_torch import (
    MAX_TAKE_SECONDS,
    F5TorchEngine,
    configure_cpu_threads,
    plan_takes,
    speak_sentences,
    torch_ode_method,
)
from app.reference import generation_frames
from app.voices import ResolvedClip


def test_f5_on_this_mac_stays_on_mlx():
    if os.name == "nt":
        assert f5_backend() == "torch"
    else:
        assert f5_backend() == "mlx"
        assert build("f5").__class__.__name__ == "F5Engine"


def test_desktop_stays_on_this_machine(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")
    monkeypatch.setenv("SKIT_HOST", "0.0.0.0")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.delenv("SKIT_DATA", raising=False)
    apply_offline()
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert os.environ["SKIT_HOST"] == "127.0.0.1"
    assert os.environ["PATH"].split(os.pathsep)[0] == str(ROOT)
    if os.name == "nt":
        assert os.environ["SKIT_DATA"].endswith("Kira vs L")
        assert os.environ["SKIT_ENGINE"] == "f5"
        assert os.environ["SKIT_F5_DEVICE"] == "cpu"
    else:
        assert "SKIT_DATA" not in os.environ


def test_windows_line_is_one_pass_until_it_is_long():
    ref_samples = 24000 * 10
    ref_text = "Kira needs a face and a name."
    text = "The records do not match. I was at home that night."
    takes = plan_takes(ref_samples, ref_text, text)
    assert takes == [
        (
            "The records do not match. I was at home that night.",
            generation_frames(ref_samples, ref_text, "The records do not match. I was at home that night."),
        )
    ]
    seen = []

    def sample(sentence, duration):
        seen.append((sentence, duration))
        return np.full(duration * 10, 0.25, dtype=np.float32)

    audio = speak_sentences(sample, ref_samples, ref_text, text)
    assert seen == takes
    assert audio.shape[0] == takes[0][1] * 10
    assert "model.infer" not in (ROOT / "app" / "engines" / "f5_torch.py").read_text(encoding="utf-8")

    long_line = " ".join(["This sentence is part of a very long speech."] * 20)
    parts = plan_takes(ref_samples, ref_text, long_line)
    assert len(parts) >= 2
    cap = int(MAX_TAKE_SECONDS * 24000 / 256)
    for spoken, frames in parts:
        assert frames <= cap or "." not in spoken[:-1]
    waves = []

    def piece(sentence, duration):
        waves.append(duration)
        return np.ones(duration, dtype=np.float32)

    joined = speak_sentences(piece, ref_samples, ref_text, long_line)
    assert waves == [frames for _spoken, frames in parts]
    assert joined.shape[0] == sum(waves)


def test_cpu_threads_stay_off_the_mac(monkeypatch):
    if os.name == "nt":
        return
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    configure_cpu_threads()
    assert "OMP_NUM_THREADS" not in os.environ


def test_windows_solver_matches_the_mac_stepper():
    assert torch_ode_method("rk4") == "rk4"
    assert torch_ode_method("midpoint") == "midpoint"
    assert torch_ode_method("euler") == "euler"
    source = (ROOT / "app" / "engines" / "f5_torch.py").read_text(encoding="utf-8")
    assert 'else "euler"' not in source


def test_windows_line_rejects_a_broken_take():
    with pytest.raises(EngineError, match="did not write"):
        speak_sentences(
            lambda _sentence, _duration: np.array([0.1, np.nan], dtype=np.float32),
            24000,
            "Hello there friend.",
            "Hello.",
        )


def test_windows_line_rejects_an_empty_take():
    with pytest.raises(EngineError, match="did not write"):
        speak_sentences(
            lambda _sentence, _duration: np.array([], dtype=np.float32),
            24000,
            "Hello there friend.",
            "Hello.",
        )


def test_mac_cache_stays_while_windows_drops_the_crossfaded_take(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIT_ENGINE", "f5")
    wav = tmp_path / "neutral.wav"
    wav.write_bytes(b"not-a-real-wav")
    clip = ResolvedClip("l", "neutral", "neutral", wav, "hello", False)
    monkeypatch.setattr("app.engines.f5_backend", lambda: "mlx")
    from app.synth import cache_key, cache_revision

    assert cache_revision() == "neutral-span-2"
    mac = cache_key(clip, "Hello there.", "neutral")
    monkeypatch.setattr("app.engines.f5_backend", lambda: "torch")
    assert cache_revision() == "neutral-span-2+torch-rk4"
    assert cache_key(clip, "Hello there.", "neutral") != mac


def test_desktop_download_refuses_other_urls():
    with pytest.raises(ValueError):
        parse_download_url("https://example.com/api/lines/abc/download?format=mp3")
    with pytest.raises(ValueError):
        parse_download_url("/api/lines/abc/audio")
    assert parse_download_url("/api/lines/abc/download?format=MP3") == ("line", "abc", "mp3")
    assert parse_download_url("http://127.0.0.1:8765/api/skits/skit-1/download?format=wav") == (
        "skit",
        "skit-1",
        "wav",
    )


def test_desktop_download_copies_the_mp3_into_downloads(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp3"
    source.write_bytes(b"ID3" + b"\x00" * 200)
    folder = tmp_path / "Downloads"
    monkeypatch.setattr("app.desktop.downloads_folder", lambda: folder)

    def fake_line(line_id, fmt):
        assert line_id == "abc"
        assert fmt == "mp3"
        return source, "audio/mpeg", "l-1.mp3"

    monkeypatch.setattr("app.service.line_download", fake_line)
    first = save_to_downloads("/api/lines/abc/download?format=mp3")
    second = save_to_downloads("/api/lines/abc/download?format=mp3")
    assert first == folder / "l-1.mp3"
    assert second == folder / "l-1 (1).mp3"
    assert first.read_bytes() == source.read_bytes()
    failed = DesktopApi().save_download("https://example.com/secret.mp3")
    assert failed["ok"] is False
    page = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "save_download" in page
    assert "pywebview" in page


def test_torch_engine_names_a_missing_checkpoint(tmp_path, monkeypatch):
    missing = tmp_path / "model_1250000.safetensors"
    monkeypatch.setenv("SKIT_F5_MODEL", str(missing))
    monkeypatch.setenv("SKIT_VOCOS", str(tmp_path / "vocos"))
    with pytest.raises(EngineError, match="model_1250000.safetensors"):
        F5TorchEngine().synthesize(
            text="Hello.",
            ref_wav=Path("missing.wav"),
            ref_text="Hello.",
            emotion="neutral",
            out_path=tmp_path / "out.wav",
        )
