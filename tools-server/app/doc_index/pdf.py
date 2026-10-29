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
    """Таблицы страницы в Markdown. Медленно (~2 с на страницу), поэтому только по запросу."""
    with _lock, pymupdf.open(path) as doc:
        page = doc[n - 1]
        out = []
        for t in page.find_tables().tables:
            try:
                md = t.to_markdown(clean=False)
            except Exception:
                rows = t.extract()
                md = "\n".join("| " + " | ".join((c or "").replace("\n", " ") for c in r) + " |" for r in rows)
            if md.strip():
                out.append(md.strip())
        return out


def render_png(path: Path, n: int, dpi: int = 200) -> bytes:
    with _lock, pymupdf.open(path) as doc:
        return doc[n - 1].get_pixmap(dpi=dpi).tobytes("png")
