"""SQLite storage: table status-change events and uploaded video metadata.

One connection shared by the pipeline thread and the API, guarded by a lock.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.occupancy import OccupancyEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    TEXT,
    table_id    TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    old_status  TEXT NOT NULL,
    new_status  TEXT NOT NULL,
    timestamp   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_time ON events (timestamp);
CREATE INDEX IF NOT EXISTS events_by_video ON events (video_id, timestamp);

CREATE TABLE IF NOT EXISTS videos (
    id                TEXT PRIMARY KEY,
    original_name     TEXT NOT NULL,
    filename          TEXT NOT NULL UNIQUE,
    size_bytes        INTEGER NOT NULL,
    duration_seconds  REAL,
    width             INTEGER,
    height            INTEGER,
    fps               REAL,
    codec             TEXT,
    uploaded_at       REAL NOT NULL
);
"""
_VIDEO_COLUMNS = (
    "id", "original_name", "filename", "size_bytes", "duration_seconds",
    "width", "height", "fps", "codec", "uploaded_at",
)


class Database:
    """Thin wrapper around the SQLite file."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")  # readers do not block the writer
            self._conn.executescript(_SCHEMA)

    def add_events(self, video_id: str | None, events: list[OccupancyEvent],
                   table_names: dict[str, str]) -> list[dict]:
        """Store status changes; returns them as rows (with their new IDs)."""
        rows = []
        with self._lock, self._conn:
            for event in events:
                row = {
                    "video_id": video_id,
                    "table_id": event.table_id,
                    "table_name": table_names.get(event.table_id, event.table_id),
                    "old_status": event.old_status.value,
                    "new_status": event.new_status.value,
                    "timestamp": event.timestamp,
                }
                cursor = self._conn.execute(
                    "INSERT INTO events (video_id, table_id, table_name, old_status, new_status, timestamp) "
                    "VALUES (:video_id, :table_id, :table_name, :old_status, :new_status, :timestamp)",
                    row,
                )
                rows.append({"id": cursor.lastrowid, **row})
        return rows

    def recent_events(self, limit: int = 50, video_id: str | None = None) -> list[dict]:
        """Newest events first, optionally for one video only."""
        query = "SELECT * FROM events"
        params: tuple = ()
        if video_id is not None:
            query += " WHERE video_id = ?"
            params = (video_id,)
        query += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            return [dict(row) for row in self._conn.execute(query, (*params, limit))]

    # ------------------------------------------------------------------ videos

    def add_video(self, video: dict) -> dict:
        """Store a video's metadata (keys: see the videos table)."""
        row = {column: video.get(column) for column in _VIDEO_COLUMNS}
        placeholders = ", ".join(f":{column}" for column in _VIDEO_COLUMNS)
        with self._lock, self._conn:
            self._conn.execute(f"INSERT INTO videos ({', '.join(_VIDEO_COLUMNS)}) VALUES ({placeholders})", row)
        return row

    def list_videos(self) -> list[dict]:
        """All videos, newest upload first."""
        with self._lock:
            return [dict(row) for row in self._conn.execute("SELECT * FROM videos ORDER BY uploaded_at DESC")]

    def get_video(self, video_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return dict(row) if row else None

    def delete_video(self, video_id: str) -> bool:
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM videos WHERE id = ?", (video_id,)).rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
