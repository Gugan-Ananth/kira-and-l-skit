"""What the speech engine is doing, so the page can show it while a line runs."""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_ACTIVE = False
_PHASE = "idle"
_SPEAKER = ""
_TAKE = 1
_TAKES = 1
_STEP = 0
_STEPS = 1


def start(speaker: str) -> None:
    """Begin a line. Later updates are ignored until this is called."""

    global _ACTIVE, _PHASE, _SPEAKER, _TAKE, _TAKES, _STEP, _STEPS
    with _LOCK:
        _ACTIVE = True
        _PHASE = "loading"
        _SPEAKER = speaker
        _TAKE = 1
        _TAKES = 1
        _STEP = 0
        _STEPS = 1


def clear() -> None:
    global _ACTIVE, _PHASE, _SPEAKER, _TAKE, _TAKES, _STEP, _STEPS
    with _LOCK:
        _ACTIVE = False
        _PHASE = "idle"
        _SPEAKER = ""
        _TAKE = 1
        _TAKES = 1
        _STEP = 0
        _STEPS = 1


def phase(name: str) -> None:
    """loading, preparing, speaking, or saving."""

    global _PHASE, _STEP, _STEPS
    with _LOCK:
        if not _ACTIVE:
            return
        _PHASE = name
        if name != "speaking":
            _STEP = 0
            _STEPS = 1


def note_take(index: int, count: int) -> None:
    """One rk4 pass inside the current line."""

    global _PHASE, _TAKE, _TAKES, _STEP, _STEPS
    with _LOCK:
        if not _ACTIVE:
            return
        _PHASE = "speaking"
        _TAKE = max(1, index)
        _TAKES = max(1, count)
        _STEP = 0
        _STEPS = 1


def report_step(step: int, steps: int) -> None:
    """The sampler's current step. Ignored when no line is running."""

    global _PHASE, _STEP, _STEPS
    with _LOCK:
        if not _ACTIVE:
            return
        _PHASE = "speaking"
        _STEPS = max(1, steps)
        _STEP = min(_STEPS, max(0, step))


def step_index(now: float, grid: list[float]) -> int:
    """1-based step for a time on an increasing solver grid."""

    total = max(1, len(grid) - 1)
    index = 0
    for mark in grid[:-1]:
        if now + 1e-4 >= mark:
            index += 1
        else:
            break
    return min(total, max(1, index))


def _detail() -> str:
    if _PHASE == "loading":
        return "Loading the voice model"
    if _PHASE == "preparing":
        return "Preparing the voice"
    if _PHASE == "saving":
        return "Saving the line"
    if _TAKES > 1 and _STEP > 0 and _STEPS > 1:
        return f"Speaking · part {_TAKE} of {_TAKES} · step {_STEP} of {_STEPS}"
    if _TAKES > 1:
        return f"Speaking · part {_TAKE} of {_TAKES}"
    if _STEP > 0 and _STEPS > 1:
        return f"Speaking · step {_STEP} of {_STEPS}"
    return "Speaking"


def _fraction() -> float:
    if _PHASE == "loading":
        return 0.06
    if _PHASE == "preparing":
        return 0.12
    if _PHASE == "saving":
        return 0.97
    span = 0.80
    take_span = span / max(1, _TAKES)
    done = (_TAKE - 1) * take_span
    if _STEPS > 1 and _STEP > 0:
        done += (_STEP / _STEPS) * take_span
    return 0.14 + done


def snapshot() -> dict:
    with _LOCK:
        speaking = _PHASE == "speaking" and _STEPS > 1 and _STEP > 0
        return {
            "active": _ACTIVE,
            "phase": _PHASE,
            "speaker": _SPEAKER,
            "fraction": round(_fraction(), 4) if _ACTIVE else 0.0,
            "determinate": _PHASE == "saving" or speaking,
            "detail": _detail() if _ACTIVE else "",
        }
