"""Write Excel workbooks under DATA_ROOT/exports for user download."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from ..config import DATA_ROOT
from ..document_tool import resolve_under_data

_SAFE_NAME_RE = re.compile(r"[^\w.\-а-яА-ЯёЁ]+", re.UNICODE)
_SHEET_BAD = re.compile(r"[\[\]\*\/\\\?\:]")
MAX_SHEETS = 20
MAX_ROWS_PER_SHEET = 5000
MAX_COLS = 64


def _safe_filename(name: str) -> str:
    raw = (name or "").strip().replace("\\", "/").split("/")[-1]
    if not raw:
        raw = "export.xlsx"
    stem = Path(raw).stem or "export"
    stem = _SAFE_NAME_RE.sub("_", stem).strip("._")[:80] or "export"
    return f"{stem}.xlsx"


def _safe_sheet_name(name: str, used: set[str]) -> str:
    base = _SHEET_BAD.sub("", (name or "").strip())[:31] or "Sheet"
    candidate = base
    n = 2
    while candidate in used:
        suffix = f"_{n}"
        candidate = (base[: 31 - len(suffix)] + suffix)[:31]
        n += 1
    used.add(candidate)
    return candidate


def _cell_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (bool, int, float)):
        return v
    return str(v)


def write_excel(
    *,
    filename: str,
    sheets: list[dict[str, Any]],
    public_base_url: str = "",
) -> dict[str, Any]:
    """
    Create .xlsx under exports/YYYY-MM-DD/.

    sheets: [{name, headers: [...], rows: [[...], ...]}, ...]
    """
    if not sheets:
        raise ValueError("At least one sheet is required")
    if len(sheets) > MAX_SHEETS:
        raise ValueError(f"Too many sheets (max {MAX_SHEETS})")

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = DATA_ROOT / "exports" / day
    out_dir.mkdir(parents=True, exist_ok=True)

    fname = _safe_filename(filename)
    dest = out_dir / fname
    if dest.exists():
        stem = dest.stem
        ts = datetime.now(timezone.utc).strftime("%H%M%S")
        dest = out_dir / f"{stem}_{ts}.xlsx"

    wb = Workbook()
    # remove default sheet after we add real ones
    default = wb.active
    used_names: set[str] = set()
    sheet_stats: list[dict[str, Any]] = []

    first = True
    for i, spec in enumerate(sheets):
        headers = list(spec.get("headers") or [])
        rows = list(spec.get("rows") or [])
        if len(headers) > MAX_COLS:
            raise ValueError(f"Sheet {i}: too many columns (max {MAX_COLS})")
        if len(rows) > MAX_ROWS_PER_SHEET:
            raise ValueError(
                f"Sheet {i}: too many rows (max {MAX_ROWS_PER_SHEET})"
            )

        title = _safe_sheet_name(str(spec.get("name") or f"Sheet{i + 1}"), used_names)
        if first:
            ws = default
            ws.title = title
            first = False
        else:
            ws = wb.create_sheet(title=title)

        bold = Font(bold=True)
        for col, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col, value=_cell_value(h))
            cell.font = bold

        for r_idx, row in enumerate(rows, start=2):
            cells = list(row) if isinstance(row, (list, tuple)) else [row]
            for c_idx, val in enumerate(cells[: max(len(headers), MAX_COLS)], start=1):
                ws.cell(row=r_idx, column=c_idx, value=_cell_value(val))

        # rough column widths
        widths = [len(str(h or "")) for h in headers] or [10]
        for row in rows[:200]:
            cells = list(row) if isinstance(row, (list, tuple)) else [row]
            for c_idx, val in enumerate(cells[: len(widths)]):
                widths[c_idx] = max(widths[c_idx], min(60, len(str(val or ""))))
        for c_idx, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(c_idx)].width = min(40, max(10, w + 2))

        sheet_stats.append(
            {
                "name": title,
                "columns": len(headers),
                "rows": len(rows),
            }
        )

    if first:
        # no sheets written somehow
        raise ValueError("No sheets written")

    wb.save(dest)
    rel = str(dest.relative_to(DATA_ROOT)).replace("\\", "/")
    base = (public_base_url or "").rstrip("/")
    download_url = (
        f"{base}/download_file?path={quote(rel)}" if base else f"/download_file?path={quote(rel)}"
    )

    return {
        "ok": True,
        "path": rel,
        "filename": dest.name,
        "size_bytes": dest.stat().st_size,
        "sheets": sheet_stats,
        "download_url": download_url,
        "note": (
            "File saved. Give the user download_url as a markdown link "
            "[скачать Excel](url) so they can open it in the browser."
        ),
    }


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
