"""Create XLSX workbooks and save them through the common verified writer."""
from __future__ import annotations

import re
from typing import Any
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from ...config import DATA_ROOT
from .export import export_dest, make_download_url

_SHEET_BAD = re.compile(r"[\[\]\*\/\\\?\:]")
MAX_SHEETS = 20
MAX_ROWS_PER_SHEET = 5000
MAX_COLS = 64

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

    dest = export_dest(filename)

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

    from .write import save_verified
    try:
        verification = save_verified(wb, dest)
    finally:
        wb.close()
    rel = str(dest.relative_to(DATA_ROOT)).replace("\\", "/")
    download_url = make_download_url(rel, public_base_url)

    return {
        "ok": True,
        "verification": verification,
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
