from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Repository:
    """Журнал запусков в SQLite. Память о площадках появится отдельно, когда будем её проектировать."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT,
                    url TEXT,
                    status TEXT,
                    actions INTEGER,
                    llm_calls INTEGER,
                    result_count INTEGER
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def add_run(self, url: str, status: str, actions: int, llm_calls: int, result_count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (created_at, url, status, actions, llm_calls, result_count) VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), url, status, actions, llm_calls, result_count),
            )
