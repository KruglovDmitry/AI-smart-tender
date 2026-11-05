"""Output paths and download links for spreadsheet artifacts."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from ...config import DATA_ROOT
from .. import resolve_under_data

_SAFE_NAME_RE = re.compile(r"[^\w.\-а-яА-ЯёЁ]+", re.UNICODE)

def _safe_filename(name: str) -> str:
    raw = (name or "").strip().replace("\\", "/").split("/")[-1]
    if not raw:
        raw = "export.xlsx"
    stem = Path(raw).stem or "export"
    stem = _SAFE_NAME_RE.sub("_", stem).strip("._")[:80] or "export"
    return f"{stem}.xlsx"


def export_dest(filename: str) -> Path:
    """Свободное имя файла в exports/YYYY-MM-DD/."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = DATA_ROOT / "exports" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / _safe_filename(filename)
    while dest.exists():
        ts = uuid.uuid4().hex[:12]
        dest = out_dir / f"{dest.stem}_{ts}.xlsx"
    return dest


def make_download_url(rel: str, public_base_url: str = "") -> str:
    base = (public_base_url or "").rstrip("/")
    return f"{base}/download_file?path={quote(rel)}"


def resolve_export_file(relative_path: str) -> Path:
    """Resolve path under DATA_ROOT; must be a file inside exports/."""
    path = resolve_under_data(relative_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"File not found: {relative_path}")
    try:
        path.relative_to((DATA_ROOT / "exports").resolve())
    except ValueError as e:
        raise PermissionError("Only files under exports/ can be downloaded") from e
    if path.suffix.lower() not in {".xlsx", ".xls", ".csv"}:
        raise PermissionError("Only spreadsheet files can be downloaded")
    return path
