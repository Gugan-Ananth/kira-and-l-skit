"""Open the skit page in a desktop window. The server stays on this machine."""

from __future__ import annotations

import os
import re
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.config import ROOT

_DOWNLOAD_PATH = re.compile(r"^/api/(lines|skits)/([^/]+)/download$")


def _keep_stdio() -> None:
    """pythonw.exe has no console. print() crashes when stdout is None."""

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _note(message: str) -> None:
    _keep_stdio()
    print(message, file=sys.stderr)
    try:
        folder = Path(os.environ.get("SKIT_DATA", str(ROOT / "data")))
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "desktop.log").open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except OSError:
        pass


def prepare_windows_dlls() -> None:
    """TorchCodec needs the shared FFmpeg DLLs. Python 3.8+ does not search PATH for them."""

    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    for folder in (ROOT / "ffmpeg", ROOT):
        if not folder.is_dir() or not any(folder.glob("avcodec-*.dll")):
            continue
        try:
            os.add_dll_directory(str(folder))
        except OSError:
            pass
        prefix = str(folder)
        current = os.environ.get("PATH", "")
        parts = [part for part in current.split(os.pathsep) if part]
        if not parts or os.path.normcase(parts[0]) != os.path.normcase(prefix):
            os.environ["PATH"] = prefix if not current else prefix + os.pathsep + current
        return


def apply_offline() -> None:
    """No model downloads. On Windows, skits go in the user profile, not Program Files."""

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["SKIT_HOST"] = "127.0.0.1"
    prepare_windows_dlls()
    root = str(ROOT)
    current = os.environ.get("PATH", "")
    if current.split(os.pathsep)[0] != root:
        os.environ["PATH"] = root if not current else root + os.pathsep + current
    if os.name == "nt" and not os.environ.get("SKIT_DATA"):
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        os.environ["SKIT_DATA"] = str(Path(base) / "Kira vs L")
    if os.name == "nt":
        os.environ["HF_HOME"] = str(Path(os.environ["SKIT_DATA"]) / "cache" / "huggingface")
        os.environ["SKIT_ENGINE"] = "f5"
        os.environ["SKIT_F5_DEVICE"] = "cpu"
        # Before torch is imported, so the ODE uses the cores.
        from app.engines.f5_torch import configure_cpu_threads

        configure_cpu_threads()


def downloads_folder() -> Path:
    """The real Downloads folder, including one the user has moved."""

    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                raw, _kind = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
            folder = Path(os.path.expandvars(str(raw)))
            if folder.drive or folder.is_absolute():
                return folder
        except OSError:
            pass
    return Path.home() / "Downloads"


def parse_download_url(url: str) -> tuple[str, str, str]:
    """A line or skit download on this app. Anything else is refused."""

    parsed = urllib.parse.urlsplit((url or "").strip())
    host = parsed.hostname or ""
    if parsed.scheme not in {"", "http"} or (parsed.netloc and host not in {"127.0.0.1", "localhost"}):
        raise ValueError("That download is not from this app.")
    match = _DOWNLOAD_PATH.fullmatch(parsed.path)
    if match is None:
        raise ValueError("That download is not from this app.")
    fmt = (urllib.parse.parse_qs(parsed.query).get("format") or ["wav"])[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9]{1,8}", fmt):
        raise ValueError("Choose a listed format.")
    kind = "line" if match.group(1) == "lines" else "skit"
    return kind, match.group(2), fmt


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for number in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"Could not save {path.name}.")


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", Path(name).name).strip(" .")
    return cleaned or "audio"


def save_to_downloads(url: str) -> Path:
    """Copy one generated file into the user's Downloads folder."""

    from app.service import SkitError, line_download, skit_download

    kind, ident, fmt = parse_download_url(url)
    try:
        if kind == "line":
            source, _media, filename = line_download(ident, fmt)
        else:
            source, _media, filename = skit_download(ident, fmt)
    except SkitError as exc:
        raise RuntimeError(str(exc)) from exc
    folder = downloads_folder()
    folder.mkdir(parents=True, exist_ok=True)
    dest = _unique_path(folder / _safe_filename(filename))
    shutil.copyfile(source, dest)
    return dest


class DesktopApi:
    """Exposed to the page. WebView2 does not finish a blob download."""

    def save_download(self, url: str) -> dict:
        try:
            dest = save_to_downloads(url)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "name": dest.name}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_up(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.1)
    raise RuntimeError(f"Kira vs L did not start on 127.0.0.1:{port}.")


def main() -> None:
    _keep_stdio()
    apply_offline()
    os.chdir(ROOT)
    port = free_port()
    os.environ["SKIT_PORT"] = str(port)

    import uvicorn

    from app.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="skit-server", daemon=True)
    thread.start()
    try:
        _wait_until_up(port)
    except RuntimeError as exc:
        server.should_exit = True
        thread.join(timeout=5)
        raise

    try:
        import webview
    except ImportError as exc:
        server.should_exit = True
        thread.join(timeout=5)
        raise SystemExit("The desktop window needs pywebview. Install requirements-windows.txt.") from exc

    window = webview.create_window(
        "Kira vs L",
        f"http://127.0.0.1:{port}",
        js_api=DesktopApi(),
        width=1100,
        height=760,
    )
    webview.start()
    del window
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    try:
        main()
    except (Exception, SystemExit) as exc:
        if not isinstance(exc, SystemExit) or exc.code not in (0, None):
            _note(f"{type(exc).__name__}: {exc}")
        raise
