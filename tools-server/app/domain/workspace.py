"""Per-tender workspace helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def safe_tender_dirname(tender_id: str) -> str:
    tid = (tender_id or "").strip() or "unknown"
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in tid)
    return cleaned[:120] or "unknown"


def tender_dir(root: Path, tender_id: str) -> Path:
    folder = Path(root) / safe_tender_dirname(tender_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def switch_downloads(rt: Any, folder: Path) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rt.downloads_dir = folder
    return folder
