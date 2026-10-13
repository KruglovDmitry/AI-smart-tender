from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class SiteProfile:
    host: str
    search_placeholder: str = ""
    card_selector: str = ""
    next_label: str = ""
    checked_at: str = ""
    success_count: int = 0
    error_count: int = 0


def profile_is_fresh(page_kind: str, selector_count: int | None) -> bool:
    if page_kind != "tender_list" or selector_count is None:
        return True
    return selector_count > 0


class Repository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS site_profiles (
                    host TEXT PRIMARY KEY,
                    search_placeholder TEXT,
                    card_selector TEXT,
                    next_label TEXT,
                    checked_at TEXT,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
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

    def load_profile(self, host: str) -> SiteProfile | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT host, search_placeholder, card_selector, next_label, checked_at, success_count, error_count FROM site_profiles WHERE host = ?",
                (host,),
            ).fetchone()
        if row is None:
            return None
        return SiteProfile(*row)

    def save_profile(self, profile: SiteProfile) -> None:
        profile.checked_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO site_profiles (host, search_placeholder, card_selector, next_label, checked_at, success_count, error_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(host) DO UPDATE SET
                    search_placeholder = excluded.search_placeholder,
                    card_selector = excluded.card_selector,
                    next_label = excluded.next_label,
                    checked_at = excluded.checked_at,
                    success_count = excluded.success_count,
                    error_count = excluded.error_count
                """,
                (
                    profile.host,
                    profile.search_placeholder,
                    profile.card_selector,
                    profile.next_label,
                    profile.checked_at,
                    profile.success_count,
                    profile.error_count,
                ),
            )

    def add_error(self, host: str) -> None:
        current = self.load_profile(host) or SiteProfile(host=host)
        current.error_count += 1
        self.save_profile(current)

    def add_run(self, url: str, status: str, actions: int, llm_calls: int, result_count: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (created_at, url, status, actions, llm_calls, result_count) VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), url, status, actions, llm_calls, result_count),
            )
