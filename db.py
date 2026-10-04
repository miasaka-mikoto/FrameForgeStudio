from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    project_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    aspect_ratio TEXT NOT NULL,
    target_fps INTEGER NOT NULL,
    work_fps INTEGER NOT NULL,
    final_fps INTEGER NOT NULL,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    character_id TEXT NOT NULL,
    name TEXT NOT NULL,
    reference_json TEXT NOT NULL DEFAULT '{}',
    body_proportion TEXT DEFAULT '',
    color_reference TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    prompt TEXT DEFAULT '',
    negative_prompt TEXT DEFAULT '',
    locked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, character_id)
);

CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    scene_id TEXT NOT NULL,
    name TEXT NOT NULL,
    reference_path TEXT DEFAULT '',
    time_of_day TEXT DEFAULT '',
    weather TEXT DEFAULT '',
    lighting TEXT DEFAULT '',
    palette TEXT DEFAULT '',
    description TEXT DEFAULT '',
    camera_info TEXT DEFAULT '',
    prompt TEXT DEFAULT '',
    negative_prompt TEXT DEFAULT '',
    UNIQUE(scene_id),
    UNIQUE(project_id, scene_id)
);

CREATE TABLE IF NOT EXISTS shots (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    scene_id TEXT NOT NULL REFERENCES scenes(scene_id) ON DELETE CASCADE,
    shot_id TEXT NOT NULL,
    name TEXT NOT NULL,
    start_time REAL NOT NULL DEFAULT 0,
    end_time REAL NOT NULL DEFAULT 1,
    fps INTEGER NOT NULL DEFAULT 12,
    camera_type TEXT DEFAULT '',
    framing TEXT DEFAULT '',
    camera_motion TEXT DEFAULT '',
    characters_json TEXT NOT NULL DEFAULT '[]',
    action TEXT DEFAULT '',
    emotion TEXT DEFAULT '',
    prompt TEXT DEFAULT '',
    negative_prompt TEXT DEFAULT '',
    storyboard_path TEXT DEFAULT '',
    UNIQUE(project_id, shot_id)
);

CREATE TABLE IF NOT EXISTS frames (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    scene_id TEXT NOT NULL,
    shot_id TEXT NOT NULL,
    frame_no INTEGER NOT NULL,
    frame_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Draft',
    prompt TEXT DEFAULT '',
    seed INTEGER,
    reference_json TEXT NOT NULL DEFAULT '{}',
    is_keyframe INTEGER NOT NULL DEFAULT 0,
    is_approved INTEGER NOT NULL DEFAULT 0,
    is_locked INTEGER NOT NULL DEFAULT 0,
    issue TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, shot_id, frame_no)
);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    template_type TEXT NOT NULL,
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generation_jobs (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    shot_id TEXT,
    frame_no INTEGER,
    status TEXT NOT NULL,
    provider TEXT NOT NULL,
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS audio (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    offset REAL NOT NULL DEFAULT 0,
    volume REAL NOT NULL DEFAULT 1,
    muted INTEGER NOT NULL DEFAULT 0,
    duration REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subtitles (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    text TEXT NOT NULL,
    position TEXT NOT NULL DEFAULT 'bottom-left'
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    action TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @contextmanager
    def transaction(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, tuple(params))
        self.conn.commit()
        return cur

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        self.conn.executemany(sql, rows)
        self.conn.commit()

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(sql, tuple(params)).fetchall()]

    def json_value(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)
