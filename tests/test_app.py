import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _wav(path: Path, seconds: float = 0.2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 24000
    frames = int(rate * seconds)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    voices = tmp_path / "voices"
    monkeypatch.setenv("SKIT_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("SKIT_VOICES", str(voices))
    monkeypatch.setenv("SKIT_ENGINE", "fake")
    monkeypatch.setenv("SKIT_PASSWORD", "")
    monkeypatch.setenv("SKIT_HOST", "127.0.0.1")
    _wav(voices / "l" / "neutral.wav")
    _wav(voices / "kira" / "neutral.wav")
    (voices / "l" / "neutral.txt").write_text("This is L.\n", encoding="utf-8")
    (voices / "kira" / "neutral.txt").write_text("This is Kira.\n", encoding="utf-8")
    from app.main import app

    return TestClient(app)


def test_speech_progress_advances_through_the_steps():
    from app.progress import clear, note_take, phase, report_step, snapshot, start, step_index

    clear()
    assert snapshot()["active"] is False
    assert step_index(0.0, [0.0, 0.5, 1.0]) == 1
    assert step_index(0.25, [0.0, 0.5, 1.0]) == 1
    assert step_index(1.0, [0.0, 0.5, 1.0]) == 2
    start("l")
    phase("loading")
    loading = snapshot()
    assert loading["detail"] == "Loading the voice model"
    assert loading["determinate"] is False
    phase("speaking")
    note_take(1, 1)
    report_step(4, 8)
    speaking = snapshot()
    assert "step 4 of 8" in speaking["detail"]
    assert speaking["determinate"] is True
    assert 0.4 < speaking["fraction"] < 0.7
    note_take(2, 2)
    report_step(8, 8)
    assert "part 2 of 2" in snapshot()["detail"]
    phase("saving")
    assert snapshot()["detail"] == "Saving the line"
    clear()
    assert snapshot()["active"] is False
    report_step(3, 8)
    assert snapshot()["active"] is False


def test_page_and_voice_bank(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "New skit" in page.text
    assert "/static/art/pair.png" in page.text
    assert "/static/art/l-icon.png" in page.text
    assert "/static/art/kira-icon.png" in page.text
    assert "l-crouch.png" not in page.text
    assert "Full audio" not in page.text
    assert 'id="mix-audio"' in page.text
    assert 'id="progress"' in page.text
    assert client.get("/api/progress").json()["active"] is False
    script = client.get("/static/app.js").text
    assert "Generate every line to hear the full audio." not in script
    assert client.get("/static/art/kira.png").status_code == 200
    assert client.get("/static/art/l-icon.png").status_code == 200
    assert client.get("/static/art/kira-icon.png").status_code == 200
    meta = client.get("/api/meta").json()
    assert meta["text_limit"] == 2000
    assert meta["emotions"] == ["neutral"]
    bank = client.get("/api/voices").json()
    assert bank["emotions"] == ["neutral"]
    assert bank["speakers"]["l"]["neutral"]["present"] is True
    assert bank["speakers"]["l"]["neutral"]["transcript"] is True


def test_write_generate_export_and_saved_take(client, tmp_path):
    skit = client.post("/api/skits", json={"title": "Rooftop"}).json()
    first = client.post(
        f"/api/skits/{skit['id']}/lines",
        json={"speaker": "l", "text": "You are nervous.", "emotion": "neutral", "pause_ms": 200},
    ).json()
    second = client.post(
        f"/api/skits/{skit['id']}/lines",
        json={"speaker": "kira", "text": "I am bored.", "emotion": "neutral", "pause_ms": 100},
    ).json()
    rejected = client.post(
        f"/api/skits/{skit['id']}/lines",
        json={"speaker": "l", "text": "No.", "emotion": "angry", "pause_ms": 0},
    )
    assert rejected.status_code == 400

    generated = client.post(f"/api/lines/{first['id']}/generate")
    assert generated.status_code == 200
    assert generated.json()["status"] == "ready"
    assert generated.json()["used_emotion"] == "neutral"

    client.post(f"/api/skits/{skit['id']}/generate")
    audio = client.get(f"/api/lines/{second['id']}/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")

    stale = client.patch(f"/api/lines/{first['id']}", json={"text": "You are very nervous."})
    assert stale.json()["status"] == "stale"
    blocked = client.get(f"/api/skits/{skit['id']}/export.wav")
    assert blocked.status_code == 400

    client.post(f"/api/lines/{first['id']}/generate")
    exported = client.get(f"/api/skits/{skit['id']}/export.wav")
    assert exported.status_code == 200
    assert exported.content[:4] == b"RIFF"

    take = tmp_path / "take.wav"
    _wav(take, 0.3)
    uploaded = client.post(
        f"/api/lines/{second['id']}/saved",
        files={"file": ("take.wav", take.read_bytes(), "audio/wav")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["mode"] == "saved"
    played = client.get(f"/api/lines/{second['id']}/audio")
    assert played.status_code == 200

    back = client.post(f"/api/lines/{second['id']}/synth")
    assert back.json()["mode"] == "synth"

    moved = client.post(f"/api/lines/{second['id']}/move", json={"direction": "up"})
    assert moved.json()["lines"][0]["id"] == second["id"]

    removed = client.delete(f"/api/skits/{skit['id']}")
    assert removed.status_code == 200
    assert client.get(f"/api/skits/{skit['id']}").status_code == 404


def test_speech_stays_on_one_model_thread(client, monkeypatch):
    import threading

    from app.engines.fake import FakeEngine
    from app.synth import synthesize
    from app.voices import resolve

    seen = []
    original = FakeEngine.synthesize

    def wrapped(self, **kwargs):
        seen.append(threading.get_ident())
        return original(self, **kwargs)

    monkeypatch.setattr(FakeEngine, "synthesize", wrapped)
    clip = resolve("l", "neutral")
    caller = threading.get_ident()

    def once(text):
        synthesize(clip, text, "neutral")

    for text in ("Hello there.", "Hello again friend."):
        thread = threading.Thread(target=once, args=(text,))
        thread.start()
        thread.join()
    assert len(seen) == 2
    assert seen[0] == seen[1]
    assert seen[0] != caller


def test_download_asks_for_a_format(client):
    meta = client.get("/api/meta").json()
    assert "mp3" in meta["download_formats"]
    assert "wav" in meta["download_formats"]
    assert "flac" in meta["download_formats"]
    page = client.get("/")
    assert 'id="mix-download"' in page.text

    skit = client.post("/api/skits", json={"title": "Rooftop"}).json()
    line = client.post(
        f"/api/skits/{skit['id']}/lines",
        json={"speaker": "l", "text": "You are nervous.", "emotion": "neutral", "pause_ms": 0},
    ).json()
    early = client.get(f"/api/lines/{line['id']}/download?format=wav")
    assert early.status_code == 404
    client.post(f"/api/lines/{line['id']}/generate")

    rejected = client.get(f"/api/lines/{line['id']}/download?format=aiff")
    assert rejected.status_code == 400

    wav = client.get(f"/api/lines/{line['id']}/download?format=wav")
    assert wav.status_code == 200
    assert wav.headers["content-type"].startswith("audio/wav")
    assert 'filename="l-1.wav"' in wav.headers["content-disposition"]
    assert wav.content[:4] == b"RIFF"

    mp3 = client.get(f"/api/lines/{line['id']}/download?format=mp3")
    assert mp3.status_code == 200
    assert mp3.headers["content-type"].startswith("audio/mpeg")
    assert len(mp3.content) > 100

    flac = client.get(f"/api/lines/{line['id']}/download?format=flac")
    assert flac.status_code == 200
    assert flac.content[:4] == b"fLaC"

    exported = client.get(f"/api/skits/{skit['id']}/download?format=mp3")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("audio/mpeg")
    assert 'filename="Rooftop.mp3"' in exported.headers["content-disposition"]
    assert len(exported.content) > 100


def test_uncaught_failure_reaches_the_page(client, monkeypatch):
    missing = client.get("/api/skits/missing-skit")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Skit not found."

    def boom(*_args, **_kwargs):
        raise RuntimeError("cuda missing")

    monkeypatch.setattr("app.main.generate_one", boom)
    skit = client.post("/api/skits", json={"title": "Crash"}).json()
    line = client.post(
        f"/api/skits/{skit['id']}/lines",
        json={"speaker": "l", "text": "Hello.", "emotion": "neutral", "pause_ms": 0},
    ).json()
    failed = client.post(f"/api/lines/{line['id']}/generate")
    assert failed.status_code == 500
    assert failed.headers["content-type"].startswith("application/json")
    assert failed.json()["detail"] == "RuntimeError: cuda missing"

    def explode():
        raise RuntimeError("list broke")

    monkeypatch.setattr("app.main.list_skits", explode)
    from app.main import app

    quiet = TestClient(app, raise_server_exceptions=False)
    crashed = quiet.get("/api/skits")
    assert crashed.status_code == 500
    assert crashed.headers["content-type"].startswith("application/json")
    assert crashed.json()["detail"] == "RuntimeError: list broke"


def test_torchcodec_failure_stays_short(client, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError(
            "Failed to create AudioDecoder for C:\\Temp\\x.wav: Could not load libtorchcodec. "
            "Likely causes:\n1. FFmpeg is not properly installed.\n"
            "Traceback (most recent call last):\n"
            "OSError: Could not load this library: libtorchcodec_core9.dll\n" * 20
        )

    monkeypatch.setattr("app.service.synthesize", boom)
    skit = client.post("/api/skits", json={"title": "Decoder"}).json()
    line = client.post(
        f"/api/skits/{skit['id']}/lines",
        json={"speaker": "l", "text": "Hello.", "emotion": "neutral", "pause_ms": 0},
    ).json()
    failed = client.post(f"/api/lines/{line['id']}/generate")
    assert failed.status_code == 500
    assert failed.json()["detail"] == (
        "Could not load the speech decoder. FFmpeg's shared libraries are missing."
    )
    stored = client.get(f"/api/skits/{skit['id']}").json()
    assert stored["lines"][0]["error"] == failed.json()["detail"]
    assert "libtorchcodec_core9" not in stored["lines"][0]["error"]


def test_generate_shows_the_real_failure(client, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("vocoder exploded")

    monkeypatch.setattr("app.service.synthesize", boom)
    skit = client.post("/api/skits", json={"title": "Boom"}).json()
    line = client.post(
        f"/api/skits/{skit['id']}/lines",
        json={"speaker": "l", "text": "Hello.", "emotion": "neutral", "pause_ms": 0},
    ).json()
    failed = client.post(f"/api/lines/{line['id']}/generate")
    assert failed.status_code == 500
    assert failed.json()["detail"] == "RuntimeError: vocoder exploded"
    stored = client.get(f"/api/skits/{skit['id']}").json()
    assert stored["lines"][0]["error"] == "RuntimeError: vocoder exploded"


def test_missing_clip_is_an_error(client, monkeypatch, tmp_path):
    empty = tmp_path / "empty-voices"
    empty.mkdir()
    monkeypatch.setenv("SKIT_VOICES", str(empty))
    skit = client.post("/api/skits", json={"title": "Empty"}).json()
    line = client.post(
        f"/api/skits/{skit['id']}/lines",
        json={"speaker": "l", "text": "Hello.", "emotion": "neutral", "pause_ms": 0},
    ).json()
    failed = client.post(f"/api/lines/{line['id']}/generate")
    assert failed.status_code == 400
    stored = client.get(f"/api/skits/{skit['id']}").json()
    assert stored["lines"][0]["status"] == "error"


def test_password_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIT_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("SKIT_VOICES", str(tmp_path / "voices"))
    monkeypatch.setenv("SKIT_ENGINE", "fake")
    monkeypatch.setenv("SKIT_PASSWORD", "desk-secret")
    from app.main import app

    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/api/meta").status_code == 401
    bad = client.post("/api/login", json={"password": "nope"})
    assert bad.status_code == 401
    good = client.post("/api/login", json={"password": "desk-secret"})
    assert good.status_code == 200
    assert client.get("/api/meta").json()["engine"] == "fake"


def test_refuses_open_bind(monkeypatch):
    monkeypatch.setenv("SKIT_HOST", "0.0.0.0")
    monkeypatch.delenv("SKIT_PASSWORD", raising=False)
    from app.config import exposed, password

    assert exposed() is True
    assert password() == ""
