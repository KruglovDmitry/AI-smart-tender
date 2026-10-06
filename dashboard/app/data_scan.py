"""Scan /data for tenders and recent agent log lines."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _mtime_iso(path: Path) -> str | None:
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _count_files(folder: Path) -> int:
    n = 0
    try:
        for p in folder.rglob("*"):
            if p.is_file() and p.name.lower() != "manifest.json":
                n += 1
    except OSError:
        return 0
    return n


def _read_manifest(folder: Path) -> dict:
    mf = folder / "manifest.json"
    if not mf.is_file():
        return {}
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def scan_tenders(data_root: Path, limit: int = 200) -> list[dict]:
    """Find tender folders under data/tenders (dirs with files or manifest.json)."""
    root = data_root / "tenders"
    if not root.is_dir():
        return []

    rows: list[dict] = []
    try:
        platform_dirs = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []

    for platform_dir in platform_dirs:
        if not platform_dir.is_dir() or platform_dir.name.startswith("_"):
            continue
        try:
            children = list(platform_dir.iterdir())
        except OSError:
            continue

        # Flat layout: platform/tender_id/
        tender_dirs = [c for c in children if c.is_dir()]
        if not tender_dirs:
            # platform_dir itself may be a tender folder
            files = [c for c in children if c.is_file()]
            if files or (platform_dir / "manifest.json").is_file():
                tender_dirs = [platform_dir]

        for tdir in tender_dirs:
            manifest = _read_manifest(tdir)
            file_count = _count_files(tdir)
            has_manifest = bool(manifest) or (tdir / "manifest.json").is_file()
            if file_count == 0 and not has_manifest:
                continue

            if tdir.parent == root:
                platform = str(manifest.get("platform") or tdir.name)
                tender_id = str(manifest.get("tender_id") or tdir.name)
                rel = f"tenders/{tdir.name}"
            else:
                platform = str(manifest.get("platform") or platform_dir.name)
                tender_id = str(manifest.get("tender_id") or tdir.name)
                rel = f"tenders/{platform_dir.name}/{tdir.name}"

            stamp = _mtime_iso(tdir / "manifest.json") or _mtime_iso(tdir)
            rows.append(
                {
                    "platform": platform,
                    "tender_id": tender_id,
                    "files": file_count,
                    "path": rel.replace("\\", "/"),
                    "modified_at": stamp,
                    "tender_url": manifest.get("tender_url") or "",
                }
            )

    rows.sort(key=lambda r: r.get("modified_at") or "", reverse=True)
    return rows[:limit]


def count_tender_files(tenders: list[dict]) -> int:
    return sum(int(t.get("files") or 0) for t in tenders)


def seen_tenders_count(db_path: Path) -> int | None:
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute("SELECT COUNT(*) FROM seen_tenders").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def recent_agent_logs(data_root: Path, limit: int = 30) -> list[dict]:
    """Tail recent lines from data/_logs/agent/."""
    log_dir = data_root / "_logs" / "agent"
    if not log_dir.is_dir():
        return []

    files: list[Path] = []
    try:
        for p in log_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".log", ".txt", ".jsonl", ".json"}:
                files.append(p)
    except OSError:
        return []

    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    items: list[dict] = []
    for fp in files[:8]:
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for ln in lines[-12:]:
            items.append(
                {
                    "file": str(fp.relative_to(data_root)).replace("\\", "/"),
                    "line": ln[:400],
                    "modified_at": _mtime_iso(fp),
                }
            )
            if len(items) >= limit:
                return items[-limit:]
    return items[-limit:]
