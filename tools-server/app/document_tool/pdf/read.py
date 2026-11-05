"""Извлечение PDF по страницам. Все вызовы PDF-библиотеки собраны здесь, чтобы её можно было заменить.

Сейчас PyMuPDF (AGPL: до поставки заказчику — коммерческая лицензия Artifex или замена на pypdfium2 + pdfplumber).
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

import pymupdf

BACKEND = f"pymupdf {pymupdf.VersionBind}"

# Шрифт без таблицы ToUnicode даёт «ǂǓǤǢǡǣǥ…» — символы из Latin Extended / IPA вместо кириллицы.
_GARBAGE = re.compile(r"[ƀ-˿]")
_LETTERS = re.compile(r"[A-Za-zА-Яа-яЁё]")

# PyMuPDF не любит параллельную работу с одним документом из разных потоков.
_lock = threading.Lock()


def page_record(text: str, images: int) -> dict[str, Any]:
    garbage = len(_GARBAGE.findall(text))
    letters = len(_LETTERS.findall(text))
    return {
        "text": text,
        "chars": len(text),
        "broken": garbage > 30 and garbage > letters * 0.3,
        "empty": len(text.strip()) < 20,
        "images": images,
    }


_SPACES = re.compile(r"[ \t]{3,}")


def extract_pages(path: Path) -> list[dict[str, Any]]:
    # Колонки в вёрстке каталогов дают длинные ряды пробелов: два пробела сохраняют границу, остальное — лишние токены.
    with _lock, pymupdf.open(path) as doc:
        return [page_record(_SPACES.sub("  ", p.get_text(sort=True)), images=len(p.get_images())) for p in doc]


def page_tables(path: Path, n: int) -> list[str]:
    """Compatibility wrapper; table extraction has one implementation."""
    return [t['markdown'] for t in page_structure(path, n, True)['tables']]


def render_png(path: Path, n: int, dpi: int = 200, bbox: list[float] | None = None) -> bytes:
    if not 72 <= dpi <= 300:
        raise ValueError("dpi must be between 72 and 300")
    with _lock, pymupdf.open(path) as doc:
        if not 1 <= n <= len(doc):
            raise ValueError("Page out of range")
        page = doc[n - 1]
        clip = page.rect
        if bbox is not None:
            import math
            if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox):
                raise ValueError("bbox requires four finite coordinates")
            clip = pymupdf.Rect(bbox)
            if clip.is_empty or not page.rect.contains(clip):
                raise ValueError("bbox must be a non-empty rectangle inside the page")
        # Bound memory for unusually large page dimensions.
        scale = min(dpi / 72, (16_000_000 / max(1, clip.width * clip.height)) ** 0.5)
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip).tobytes("png")


def extract_text(path: Path) -> str:
    """Простой резервный текстовый разбор; без OCR и анализа изображений."""
    from pypdf import PdfReader

    return "\n\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)


def page_structure(path: Path, n: int, include_tables: bool = True) -> dict[str, Any]:
    """Coordinates are PDF points, top-left origin. Text/table detection is heuristic."""
    with _lock, pymupdf.open(path) as doc:
        page = doc[n - 1]
        blocks = page.get_text("blocks", sort=True)
        text_blocks = [b for b in blocks if b[6] == 0]
        images = page.get_image_info()
        result = {
            "coordinate_system": "pdf_points_top_left",
            "width": page.rect.width, "height": page.rect.height,
            "blocks": [{"id": f"text-{i}", "kind": "text", "bbox": list(b[:4]),
                        "preview": b[4][:160]} for i, b in enumerate(text_blocks[:80], 1)],
            "images": [{"id": f"image-{i}", "bbox": list(im["bbox"])}
                       for i, im in enumerate(images[:30], 1)],
            "tables": [], "warnings": [],
        }
        if len(text_blocks) > 80 or len(images) > 30:
            result["warnings"].append("layout_objects_truncated")
        if include_tables:
            try:
                tables = page.find_tables().tables
                for i, table in enumerate(tables[:20], 1):
                    values = table.extract()
                    # Some backends detect a line stroke as a separate empty column.
                    # Preserve raw cells, but never silently present that grid as reliable.
                    thin = any(c is not None and 0 < c[2] - c[0] <= 3 for c in table.cells)
                    empty_column = any(all(j >= len(row) or not row[j] for row in values)
                                       for j in range(table.col_count))
                    if thin and empty_column:
                        result["warnings"].append(f"table_grid_ambiguous: table-{i}; verify with view_page")
                    result["tables"].append({"id": f"table-{i}", "bbox": list(table.bbox),
                        "rows": table.row_count, "columns": table.col_count,
                        "markdown": table.to_markdown(clean=False)})
                if len(tables) > 20:
                    result["warnings"].append("tables_truncated")
            except Exception as exc:
                result["warnings"].append(f"table_detection_failed: {type(exc).__name__}")
        result["visual_recommended"] = bool(result["warnings"] or images or not text_blocks or len(page.get_drawings()) > 15
            or any(t["columns"] >= 5 or t["rows"] >= 20 for t in result["tables"]))
        return result
