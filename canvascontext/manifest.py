from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Manifest:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                success INTEGER,
                error TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS resources (
                kind TEXT NOT NULL,
                canvas_id TEXT NOT NULL,
                local_path TEXT NOT NULL,
                updated_at TEXT,
                content_hash TEXT,
                source_url TEXT,
                last_seen_run INTEGER NOT NULL,
                stale INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (kind, canvas_id)
            )
            """
        )
        self.connection.commit()

    def begin(self) -> int:
        cursor = self.connection.execute(
            "INSERT INTO sync_runs(started_at) VALUES (?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def record(
        self,
        run_id: int,
        *,
        kind: str,
        canvas_id: str | int,
        local_path: str,
        updated_at: str | None = None,
        content_hash: str | None = None,
        source_url: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO resources(
                kind, canvas_id, local_path, updated_at, content_hash,
                source_url, last_seen_run, stale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(kind, canvas_id) DO UPDATE SET
                local_path=excluded.local_path,
                updated_at=excluded.updated_at,
                content_hash=excluded.content_hash,
                source_url=excluded.source_url,
                last_seen_run=excluded.last_seen_run,
                stale=0
            """,
            (
                kind,
                str(canvas_id),
                local_path,
                updated_at,
                content_hash,
                source_url,
                run_id,
            ),
        )

    def finish(self, run_id: int, *, success: bool, error: str = "") -> None:
        if success:
            self.connection.execute(
                "UPDATE resources SET stale=1 WHERE last_seen_run != ?", (run_id,)
            )
        self.connection.execute(
            """
            UPDATE sync_runs
            SET finished_at=?, success=?, error=?
            WHERE id=?
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                int(success),
                error,
                run_id,
            ),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Manifest":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

