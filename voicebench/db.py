from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    reference_path TEXT,
    reference_sha256 TEXT,
    reference_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    original_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, position)
);

CREATE TABLE IF NOT EXISTS engines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('mock', 'command')),
    version TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    prompt_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    engine_id INTEGER NOT NULL REFERENCES engines(id),
    seed INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, id);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    prompt_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    engine_id INTEGER NOT NULL REFERENCES engines(id),
    anonymous_code TEXT NOT NULL,
    output_path TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    duration_seconds REAL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_prompt ON runs(project_id, prompt_id, id);

CREATE TABLE IF NOT EXISTS comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    prompt_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    left_run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    right_run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    winner TEXT CHECK(winner IN ('left', 'right', 'tie', 'both_bad')),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    rated_at TEXT,
    UNIQUE(left_run_id, right_run_id),
    CHECK(left_run_id <> right_run_id)
);

CREATE TABLE IF NOT EXISTS studio_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    original_text TEXT NOT NULL,
    seed INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'studio',
    quality_mode TEXT NOT NULL DEFAULT 'standard',
    request_fingerprint TEXT,
    status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    output_path TEXT,
    output_sha256 TEXT,
    duration_seconds REAL,
    download_count INTEGER NOT NULL DEFAULT 0,
    last_downloaded_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_studio_requests_created ON studio_requests(id DESC);

CREATE TABLE IF NOT EXISTS studio_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES studio_requests(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    prompt_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    paragraph_index INTEGER NOT NULL DEFAULT 1,
    sentence_count INTEGER NOT NULL DEFAULT 1,
    semantic_role TEXT NOT NULL DEFAULT 'statement',
    paragraph_end INTEGER NOT NULL DEFAULT 0,
    pause_after_ms INTEGER,
    UNIQUE(request_id, position)
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(projects)")}
            if "reference_text" not in columns:
                connection.execute(
                    "ALTER TABLE projects ADD COLUMN reference_text TEXT NOT NULL DEFAULT ''"
                )
            studio_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(studio_requests)")
            }
            if "source" not in studio_columns:
                connection.execute(
                    "ALTER TABLE studio_requests ADD COLUMN source TEXT NOT NULL DEFAULT 'studio'"
                )
            if "quality_mode" not in studio_columns:
                connection.execute(
                    "ALTER TABLE studio_requests ADD COLUMN quality_mode TEXT NOT NULL DEFAULT 'standard'"
                )
            if "request_fingerprint" not in studio_columns:
                connection.execute(
                    "ALTER TABLE studio_requests ADD COLUMN request_fingerprint TEXT"
                )
            if "download_count" not in studio_columns:
                connection.execute(
                    "ALTER TABLE studio_requests ADD COLUMN download_count INTEGER NOT NULL DEFAULT 0"
                )
            if "last_downloaded_at" not in studio_columns:
                connection.execute(
                    "ALTER TABLE studio_requests ADD COLUMN last_downloaded_at TEXT"
                )
            segment_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(studio_segments)")
            }
            segment_migrations = {
                "paragraph_index": "INTEGER NOT NULL DEFAULT 1",
                "sentence_count": "INTEGER NOT NULL DEFAULT 1",
                "semantic_role": "TEXT NOT NULL DEFAULT 'statement'",
                "paragraph_end": "INTEGER NOT NULL DEFAULT 0",
                "pause_after_ms": "INTEGER",
            }
            for name, definition in segment_migrations.items():
                if name not in segment_columns:
                    connection.execute(
                        f"ALTER TABLE studio_segments ADD COLUMN {name} {definition}"
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_studio_requests_fingerprint "
                "ON studio_requests(request_fingerprint, source, status)"
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    def fetch_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]

    def fetch_one(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
            return dict(row) if row else None

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> int:
        with self.connect() as connection:
            cursor = connection.execute(sql, parameters)
            return int(cursor.lastrowid or 0)
