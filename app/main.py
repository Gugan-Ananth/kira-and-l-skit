"""Local skit desk. One process serves the page and the speech API."""

from __future__ import annotations

import hashlib
import hmac
import traceback

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import (
    EMOTIONS,
    ROOT,
    SPEAKER_LABELS,
    SPEAKERS,
    data_dir,
    engine_name,
    password,
)
from app.db import (
    add_line,
    create_skit,
    delete_line,
    delete_skit,
    get_line,
    get_skit,
    list_skits,
    move_line,
    rename_skit,
    update_line,
)
from app.progress import snapshot
from app.service import (
    TEXT_LIMIT,
    SkitError,
    check_emotion,
    check_pause,
    check_speaker,
    check_text,
    export_wav,
    generate_one,
    generate_skit,
    screen_message,
    line_download,
    playback_path,
    safe_title,
    skit_download,
    store_saved_take,
)
from app.audio import ready_download_formats
from app.synth import busy
from app.voices import bank_status

app = FastAPI(title="Kira vs L")
COOKIE = "skit_session"


def _failure_detail(exc: Exception) -> str:
    full = f"{type(exc).__name__}: {exc}".strip() or "The speech engine failed."
    try:
        folder = data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "desktop.log").open("a", encoding="utf-8") as handle:
            handle.write(full + "\n" + traceback.format_exc() + "\n")
    except OSError:
        pass
    return screen_message(exc)


@app.exception_handler(Exception)
async def show_failure(_request: Request, exc: Exception):
    """Unhandled crashes become JSON the page can print. HTTP errors keep their own handler."""
    return JSONResponse({"detail": _failure_detail(exc)}, status_code=500)


class SkitIn(BaseModel):
    title: str = "Untitled skit"


class LineIn(BaseModel):
    speaker: str = "l"
    text: str = ""
    emotion: str = "neutral"
    pause_ms: int = 400


class LinePatch(BaseModel):
    speaker: str | None = None
    text: str | None = None
    emotion: str | None = None
    pause_ms: int | None = None


class MoveIn(BaseModel):
    direction: str = Field(pattern="^(up|down)$")


class LoginIn(BaseModel):
    password: str


def _token(secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), b"skit-session-v1", hashlib.sha256).hexdigest()


def _same(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


@app.middleware("http")
async def require_password(request: Request, call_next):
    secret = password()
    if not secret:
        return await call_next(request)
    path = request.url.path
    if path in {"/api/login", "/api/health"} or path.startswith("/static"):
        return await call_next(request)
    cookie = request.cookies.get(COOKIE, "")
    if _same(cookie, _token(secret)):
        return await call_next(request)
    if path == "/" or path.startswith("/static"):
        return await call_next(request)
    return JSONResponse({"detail": "Sign in required."}, status_code=401)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/progress")
def speech_progress():
    return snapshot()


@app.get("/api/meta")
def meta():
    name = engine_name()
    return {
        "engine": name,
        "password": bool(password()),
        "busy": busy(),
        "speakers": [{"id": speaker, "label": SPEAKER_LABELS[speaker]} for speaker in SPEAKERS],
        "emotions": list(EMOTIONS),
        "text_limit": TEXT_LIMIT,
        "download_formats": [item["id"] for item in ready_download_formats()],
        "engine_note": _engine_note(name),
    }


def _engine_note(name: str) -> str:
    if name == "f5":
        return "F5 copies the neutral clip. The clip needs a transcript of the words in it."
    if name == "chatterbox":
        return "Chatterbox copies the clip, then the emotion pushes intensity. Turbo checkpoints can also add a sigh or a chuckle."
    return "Test engine. It writes a tone, not a cloned voice."


@app.post("/api/login")
def login(body: LoginIn, response: Response):
    secret = password()
    if not secret:
        return {"ok": True}
    if not _same(body.password, secret):
        raise HTTPException(401, "Wrong password.")
    response.set_cookie(
        COOKIE,
        _token(secret),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return {"ok": True}


@app.get("/api/voices")
def voices():
    return bank_status()


@app.get("/api/skits")
def skits():
    return list_skits()


@app.post("/api/skits")
def create(body: SkitIn):
    return create_skit(body.title)


@app.get("/api/skits/{skit_id}")
def read_skit(skit_id: str):
    skit = get_skit(skit_id)
    if skit is None:
        raise HTTPException(404, "Skit not found.")
    return skit


@app.patch("/api/skits/{skit_id}")
def patch_skit(skit_id: str, body: SkitIn):
    skit = rename_skit(skit_id, body.title)
    if skit is None:
        raise HTTPException(404, "Skit not found.")
    return skit


@app.delete("/api/skits/{skit_id}")
def remove_skit(skit_id: str):
    if not delete_skit(skit_id):
        raise HTTPException(404, "Skit not found.")
    return {"ok": True}


@app.post("/api/skits/{skit_id}/lines")
def create_line(skit_id: str, body: LineIn):
    try:
        speaker = check_speaker(body.speaker)
        emotion = check_emotion(body.emotion)
        pause_ms = check_pause(body.pause_ms)
        if body.text.strip():
            check_text(body.text)
    except SkitError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    line = add_line(
        skit_id,
        speaker=speaker,
        text=body.text.strip(),
        emotion=emotion,
        pause_ms=pause_ms,
    )
    if line is None:
        raise HTTPException(404, "Skit not found.")
    return line


@app.patch("/api/lines/{line_id}")
def patch_line(line_id: str, body: LinePatch):
    line = get_line(line_id)
    if line is None:
        raise HTTPException(404, "Line not found.")
    fields: dict = {}
    try:
        if body.speaker is not None:
            fields["speaker"] = check_speaker(body.speaker)
        if body.emotion is not None:
            fields["emotion"] = check_emotion(body.emotion)
        if body.pause_ms is not None:
            fields["pause_ms"] = check_pause(body.pause_ms)
        if body.text is not None:
            fields["text"] = body.text.strip()
            if fields["text"]:
                check_text(fields["text"])
    except SkitError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    changed = any(fields.get(key) != line[key] for key in ("speaker", "text", "emotion") if key in fields)
    if changed and line["mode"] != "saved" and line.get("audio_path"):
        fields["status"] = "stale"
        fields["error"] = None
    updated = update_line(line_id, fields)
    return updated


@app.delete("/api/lines/{line_id}")
def remove_line(line_id: str):
    if delete_line(line_id) is None:
        raise HTTPException(404, "Line not found.")
    return {"ok": True}


@app.post("/api/lines/{line_id}/move")
def reorder(line_id: str, body: MoveIn):
    direction = -1 if body.direction == "up" else 1
    line = move_line(line_id, direction)
    if line is None:
        raise HTTPException(404, "Line not found.")
    return get_skit(line["skit_id"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SkitError):
        return HTTPException(exc.status, str(exc))
    return HTTPException(500, _failure_detail(exc))


@app.post("/api/lines/{line_id}/generate")
def generate_line(line_id: str):
    try:
        return generate_one(line_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/skits/{skit_id}/generate")
def generate_all(skit_id: str):
    try:
        return generate_skit(skit_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/lines/{line_id}/saved")
async def upload_saved(line_id: str, file: UploadFile = File(...)):
    if get_line(line_id) is None:
        raise HTTPException(404, "Line not found.")
    payload = await file.read()
    if len(payload) > 20 * 1024 * 1024:
        raise HTTPException(400, "Clip is larger than 20 MB.")
    if not payload:
        raise HTTPException(400, "That file is empty.")
    from app.config import data_dir

    folder = data_dir() / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / f"{line_id}.bin"
    source.write_bytes(payload)
    try:
        return store_saved_take(line_id, source)
    except SkitError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    finally:
        source.unlink(missing_ok=True)


@app.post("/api/lines/{line_id}/synth")
def use_synth(line_id: str):
    line = get_line(line_id)
    if line is None:
        raise HTTPException(404, "Line not found.")
    fields = {"mode": "synth", "error": None}
    if line.get("audio_path"):
        fields["status"] = "stale"
    else:
        fields["status"] = "empty"
    return update_line(line_id, fields)


@app.get("/api/lines/{line_id}/download")
def download_line(line_id: str, format: str = "wav"):
    try:
        path, media, filename = line_download(line_id, format)
    except SkitError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return FileResponse(path, media_type=media, filename=filename)


@app.get("/api/skits/{skit_id}/download")
def download_skit(skit_id: str, format: str = "wav"):
    try:
        path, media, filename = skit_download(skit_id, format)
    except SkitError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return FileResponse(path, media_type=media, filename=filename)


@app.get("/api/lines/{line_id}/audio")
def line_audio(line_id: str):
    line = get_line(line_id)
    if line is None:
        raise HTTPException(404, "Line not found.")
    path = playback_path(line)
    if path is None:
        raise HTTPException(404, "This line has no audio yet.")
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/skits/{skit_id}/export.wav")
def export_skit(skit_id: str):
    try:
        path = export_wav(skit_id)
    except SkitError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    skit = get_skit(skit_id)
    filename = f"{safe_title(skit['title'])}.wav"
    return FileResponse(path, media_type="audio/wav", filename=filename)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
