"""База найденных закупок: всё, что агент вернул в прогонах, в одной SQLite.

Файл тот же, что у профилей площадок: ``data/tenders/_agent/agent.sqlite3``.

Таблицы:
- ``tenders`` — закупка на площадке. Ключ: площадка + номер, а если номера нет — ссылка.
  Пустые поля нового прогона не затирают уже известные значения; ``first_seen`` /
  ``last_seen`` / ``seen_count`` показывают, когда закупка появилась и сколько раз встречалась.
- ``tender_documents`` — документы закупки (название, ссылка, путь к скачанному файлу).
- ``search_runs`` и ``run_tenders`` — какой запрос на какой площадке вернул какие закупки
  и в каком порядке: по ним видно, что нового с прошлого прогона.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..api.schemas import DownloadRecord, TaskResult, TenderResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    tender_key TEXT NOT NULL,
    external_id TEXT,
    url TEXT,
    title TEXT,
    customer TEXT,
    price REAL,
    currency TEXT,
    published_at TEXT,
    deadline TEXT,
    status TEXT,
    verification_status TEXT,
    matched_keyword TEXT,
    sources TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE (platform, tender_key)
);
CREATE INDEX IF NOT EXISTS tenders_published ON tenders (platform, published_at);
CREATE TABLE IF NOT EXISTS tender_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER NOT NULL REFERENCES tenders(id),
    name TEXT,
    url TEXT NOT NULL,
    local_path TEXT,
    size INTEGER,
    downloaded_at TEXT,
    UNIQUE (tender_id, url)
);
CREATE TABLE IF NOT EXISTS search_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    platform TEXT,
    start_url TEXT,
    query TEXT,
    task_text TEXT,
    status TEXT,
    result_count INTEGER,
    new_count INTEGER,
    errors TEXT,
    stats TEXT,
    trace_dir TEXT
);
CREATE TABLE IF NOT EXISTS run_tenders (
    run_id INTEGER NOT NULL REFERENCES search_runs(id),
    tender_id INTEGER NOT NULL REFERENCES tenders(id),
    position INTEGER NOT NULL,
    is_new INTEGER NOT NULL,
    PRIMARY KEY (run_id, tender_id)
);
"""

_FIELDS = (
    "external_id", "url", "title", "customer", "price", "currency", "published_at",
    "deadline", "status", "verification_status", "matched_keyword",
)


def platform_of(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").lower().removeprefix("www.")


def tender_key(tender: TenderResult) -> str:
    return (tender.id or "").strip() or (tender.url or "").strip()


class TenderStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------- запись ----------

    def save_run(self, text: str, result: TaskResult, trace_dir: str | None = None) -> dict:
        """Сохраняет закупки и документы прогона. Возвращает {"run_id", "new"} для ответа агента."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        task = result.task
        start_url = task.url if task else ""
        site = platform_of(start_url)
        new_count = 0
        with self._connect() as conn:
            run_id = conn.execute(
                "INSERT INTO search_runs (created_at, platform, start_url, query, task_text, status, result_count,"
                " new_count, errors, stats, trace_dir) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (now, site, start_url, (task.keywords[0] if task and task.keywords else None), text, result.status,
                 len(result.results), json.dumps(result.errors, ensure_ascii=False),
                 json.dumps(result.stats, ensure_ascii=False), trace_dir),
            ).lastrowid
            for position, tender in enumerate(result.results, start=1):
                key = tender_key(tender)
                if not key:
                    continue
                platform = platform_of(tender.url) or site
                # Карточка на поддомене (44.sberbank-ast.ru) — всё та же площадка запуска.
                if site and (platform == site or platform.endswith("." + site)):
                    platform = site
                tender_id, is_new = self._upsert(conn, platform, key, tender, now)
                new_count += int(is_new)
                conn.execute(
                    "INSERT OR REPLACE INTO run_tenders (run_id, tender_id, position, is_new) VALUES (?, ?, ?, ?)",
                    (run_id, tender_id, position, int(is_new)),
                )
                names = {doc.get("url"): doc.get("name") for doc in tender.documents}
                for url in tender.document_urls:
                    conn.execute(
                        "INSERT INTO tender_documents (tender_id, name, url) VALUES (?, ?, ?)"
                        " ON CONFLICT(tender_id, url) DO UPDATE SET name = COALESCE(excluded.name, name)",
                        (tender_id, names.get(url), url),
                    )
                for download in result.downloads:
                    if download.status == "saved" and download.tender_url == tender.url:
                        self._save_download(conn, tender_id, download, now)
            conn.execute("UPDATE search_runs SET new_count = ? WHERE id = ?", (new_count, run_id))
        return {"run_id": run_id, "new": new_count}

    def _upsert(self, conn: sqlite3.Connection, platform: str, key: str, tender: TenderResult, now: str) -> tuple[int, bool]:
        values = {
            "external_id": tender.id,
            "url": tender.url,
            "title": tender.title,
            "customer": tender.customer,
            "price": tender.price,
            "currency": tender.currency,
            "published_at": tender.published_at,
            "deadline": tender.deadline,
            "status": tender.status,
            "verification_status": tender.verification_status,
            "matched_keyword": tender.matched_keyword,
        }
        row = conn.execute("SELECT id FROM tenders WHERE platform = ? AND tender_key = ?", (platform, key)).fetchone()
        if row is None:
            cursor = conn.execute(
                f"INSERT INTO tenders (platform, tender_key, {', '.join(_FIELDS)}, sources, first_seen, last_seen)"
                f" VALUES (?, ?, {', '.join('?' for _ in _FIELDS)}, ?, ?, ?)",
                (platform, key, *(values[f] for f in _FIELDS), json.dumps(tender.sources, ensure_ascii=False), now, now),
            )
            return int(cursor.lastrowid), True
        # Новое значение заменяет старое, пустое — не затирает.
        sets = ", ".join(f"{f} = COALESCE(?, {f})" for f in _FIELDS)
        conn.execute(
            f"UPDATE tenders SET {sets}, sources = ?, last_seen = ?, seen_count = seen_count + 1 WHERE id = ?",
            (*(values[f] for f in _FIELDS), json.dumps(tender.sources, ensure_ascii=False), now, row["id"]),
        )
        return int(row["id"]), False

    def _save_download(self, conn: sqlite3.Connection, tender_id: int, download: DownloadRecord, now: str) -> None:
        conn.execute(
            "INSERT INTO tender_documents (tender_id, name, url, local_path, size, downloaded_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(tender_id, url) DO UPDATE SET local_path = excluded.local_path, size = excluded.size,"
            " downloaded_at = excluded.downloaded_at, name = COALESCE(name, excluded.name)",
            (tender_id, download.name, download.source_url, download.path, download.size, now),
        )

    def keys_new_in_run(self, run_id: int) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT t.tender_key FROM run_tenders r JOIN tenders t ON t.id = r.tender_id WHERE r.run_id = ? AND r.is_new = 1",
                (run_id,),
            ).fetchall()
        return [row["tender_key"] for row in rows]

    # ---------- чтение ----------

    def search(self, *, platform: str | None = None, text: str | None = None, since: str | None = None,
               limit: int = 50) -> list[dict]:
        where, args = [], []
        if platform:
            where.append("t.platform LIKE ?")
            args.append(f"%{platform.lower().removeprefix('www.')}%")
        if text:
            where.append("(t.title LIKE ? OR t.customer LIKE ? OR t.external_id LIKE ?)")
            args += [f"%{text}%"] * 3
        if since:
            where.append("t.first_seen >= ?")
            args.append(since)
        sql = (
            "SELECT t.*, (SELECT json_group_array(json_object('name', d.name, 'url', d.url, 'local_path', d.local_path))"
            " FROM tender_documents d WHERE d.tender_id = t.id) AS documents FROM tenders t"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY COALESCE(t.published_at, substr(t.first_seen, 1, 10)) DESC, t.id DESC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, (*args, limit)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["sources"] = json.loads(item["sources"] or "{}")
            item["documents"] = [d for d in json.loads(item["documents"] or "[]") if d.get("url")]
            out.append(item)
        return out
