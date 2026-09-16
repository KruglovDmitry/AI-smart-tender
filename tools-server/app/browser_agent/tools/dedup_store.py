"""SQLite store for seen tenders (deduplication across platform runs)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SeenTenderStore:
    def __init__(self, db_path: Path | str) -> None:
        raw = str(db_path)
        if raw == ":memory:":
            self.db_path = "file:seen_tenders_mem?mode=memory&cache=shared"
            self._uri = True
        else:
            self.db_path = raw
            self._uri = False
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, uri=self._uri, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def close(self) -> None:
        self._conn.close()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_tenders (
                platform TEXT NOT NULL,
                tender_id TEXT NOT NULL,
                tender_url TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (platform, tender_id)
            )
            """
        )
        self._conn.commit()

    def is_seen(self, platform: str, tender_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_tenders WHERE platform = ? AND tender_id = ?",
            (platform, tender_id),
        ).fetchone()
        return row is not None

    def mark_seen(self, platform: str, tender_id: str, tender_url: str) -> dict[str, Any]:
        now = _utc_now()
        existing = self._conn.execute(
            "SELECT first_seen_at FROM seen_tenders WHERE platform = ? AND tender_id = ?",
            (platform, tender_id),
        ).fetchone()
        if existing:
            self._conn.execute(
                """
                UPDATE seen_tenders
                SET last_seen_at = ?, tender_url = ?
                WHERE platform = ? AND tender_id = ?
                """,
                (now, tender_url, platform, tender_id),
            )
            is_new = False
            first_seen = existing["first_seen_at"]
        else:
            self._conn.execute(
                """
                INSERT INTO seen_tenders
                (platform, tender_id, tender_url, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (platform, tender_id, tender_url, now, now),
            )
            is_new = True
            first_seen = now
        self._conn.commit()
        return {
            "platform": platform,
            "tender_id": tender_id,
            "tender_url": tender_url,
            "is_new": is_new,
            "first_seen_at": first_seen,
            "last_seen_at": now,
        }

    def check_tender(self, platform: str, tender_id: str, tender_url: str) -> dict[str, Any]:
        seen = self.is_seen(platform, tender_id)
        return {
            "platform": platform,
            "tender_id": tender_id,
            "tender_url": tender_url,
            "is_seen": seen,
            "is_new": not seen,
        }

    def stats(self, platform: str | None = None) -> dict[str, Any]:
        if platform:
            count = self._conn.execute(
                "SELECT COUNT(*) AS c FROM seen_tenders WHERE platform = ?",
                (platform,),
            ).fetchone()["c"]
        else:
            count = self._conn.execute("SELECT COUNT(*) AS c FROM seen_tenders").fetchone()[
                "c"
            ]
        return {"platform": platform, "seen_count": count}
