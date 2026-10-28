"""SQLite storage for skits. One file under the data directory."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS skits (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lines (
    id TEXT PRIMARY KEY,
    skit_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    speaker TEXT NOT NULL,
    text TEXT NOT NULL,
    emotion TEXT NOT NULL,
    pause_ms INTEGER NOT NULL DEFAULT 400,
    mode TEXT NOT NULL DEFAULT 'synth',
    saved_path TEXT,
    audio_path TEXT,
    audio_key TEXT,
    status TEXT NOT NULL DEFAULT 'empty',
    error TEXT,
    used_emotion TEXT,
    FOREIGN KEY (skit_id) REFERENCES skits(id) ON DELETE CASCADE
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    path = data_dir() / "skits.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _line(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def list_skits() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM skits ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def get_skit(skit_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        skit = conn.execute("SELECT * FROM skits WHERE id = ?", (skit_id,)).fetchone()
        if skit is None:
            return None
        lines = conn.execute(
            "SELECT * FROM lines WHERE skit_id = ? ORDER BY position, rowid",
            (skit_id,),
        ).fetchall()
    payload = dict(skit)
    payload["lines"] = [_line(row) for row in lines]
    return payload


def create_skit(title: str) -> dict[str, Any]:
    skit_id = uuid.uuid4().hex
    stamp = now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO skits (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (skit_id, title.strip() or "Untitled skit", stamp, stamp),
        )
    return get_skit(skit_id)


def rename_skit(skit_id: str, title: str) -> dict[str, Any] | None:
    with connect() as conn:
        found = conn.execute("SELECT id FROM skits WHERE id = ?", (skit_id,)).fetchone()
        if found is None:
            return None
        conn.execute(
            "UPDATE skits SET title = ?, updated_at = ? WHERE id = ?",
            (title.strip() or "Untitled skit", now(), skit_id),
        )
    return get_skit(skit_id)


def delete_skit(skit_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM skits WHERE id = ?", (skit_id,))
        return cur.rowcount > 0


def get_line(line_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM lines WHERE id = ?", (line_id,)).fetchone()
    return dict(row) if row else None


def add_line(
    skit_id: str,
    *,
    speaker: str,
    text: str,
    emotion: str,
    pause_ms: int,
) -> dict[str, Any] | None:
    with connect() as conn:
        skit = conn.execute("SELECT id FROM skits WHERE id = ?", (skit_id,)).fetchone()
        if skit is None:
            return None
        position = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM lines WHERE skit_id = ?",
            (skit_id,),
        ).fetchone()[0]
        line_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO lines (
                id, skit_id, position, speaker, text, emotion, pause_ms, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'empty')
            """,
            (line_id, skit_id, position, speaker, text, emotion, pause_ms),
        )
        conn.execute("UPDATE skits SET updated_at = ? WHERE id = ?", (now(), skit_id))
    return get_line(line_id)


def update_line(line_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"speaker", "text", "emotion", "pause_ms", "mode", "saved_path", "audio_path", "audio_key", "status", "error", "used_emotion"}
    sets = {key: value for key, value in fields.items() if key in allowed}
    if not sets:
        return get_line(line_id)
    assignments = ", ".join(f"{key} = ?" for key in sets)
    with connect() as conn:
        row = conn.execute("SELECT skit_id FROM lines WHERE id = ?", (line_id,)).fetchone()
        if row is None:
            return None
        conn.execute(
            f"UPDATE lines SET {assignments} WHERE id = ?",
            (*sets.values(), line_id),
        )
        conn.execute(
            "UPDATE skits SET updated_at = ? WHERE id = ?",
            (now(), row["skit_id"]),
        )
    return get_line(line_id)


def delete_line(line_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM lines WHERE id = ?", (line_id,)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM lines WHERE id = ?", (line_id,))
        conn.execute(
            """
            UPDATE lines
            SET position = position - 1
            WHERE skit_id = ? AND position > ?
            """,
            (row["skit_id"], row["position"]),
        )
        conn.execute(
            "UPDATE skits SET updated_at = ? WHERE id = ?",
            (now(), row["skit_id"]),
        )
    return dict(row)


def move_line(line_id: str, direction: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM lines WHERE id = ?", (line_id,)).fetchone()
        if row is None:
            return None
        neighbor = conn.execute(
            """
            SELECT * FROM lines
            WHERE skit_id = ? AND position = ?
            """,
            (row["skit_id"], row["position"] + direction),
        ).fetchone()
        if neighbor is None:
            return dict(row)
        conn.execute(
            "UPDATE lines SET position = ? WHERE id = ?",
            (neighbor["position"], row["id"]),
        )
        conn.execute(
            "UPDATE lines SET position = ? WHERE id = ?",
            (row["position"], neighbor["id"]),
        )
        conn.execute(
            "UPDATE skits SET updated_at = ? WHERE id = ?",
            (now(), row["skit_id"]),
        )
    return get_line(line_id)
