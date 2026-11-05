"""Dispatch file extraction to one reader per format; uploads and tools share readers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ftfy

from ..config import ALLOWED_EXTENSIONS

from .text.read import TEXT_EXTENSIONS
from .legacy.read import _extract_legacy_doc, _extract_rtf, _extract_opendocument
from .archives.read import ARCHIVE_EXTENSIONS, extract_archive as _extract_archive


def _fix(text: str) -> str:
    return ftfy.fix_text(text or "").strip()



def extract_file(path: Path) -> dict[str, Any]:
    """Вернуть извлечённый текст одного файла (как после upload в чат OWUI)."""
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")

    from .sniff import detect_extension

    # Файл без расширения или с ложным расширением — тип по содержимому.
    ext = detect_extension(path)
    if ext not in ALLOWED_EXTENSIONS and ext not in TEXT_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext or '(none)'}")

    if ext in ARCHIVE_EXTENSIONS:
        return _extract_archive(path)
    from .raster import IMAGE_EXTENSIONS
    if ext in IMAGE_EXTENSIONS:
        from .images.read import extract_parts
        parts = extract_parts(path)
        return {'path': str(path), 'filename': path.name, 'extension': ext, 'chars': 0, 'content': '',
                'frames': len(parts), 'warnings': ['Image pixels have not been read; use read_image/view_image or ocr_image.']}

    # Для разбора по расширению нужен путь с правильным суффиксом (MarkItDown, zip-форматы).
    work = path
    tmp: Path | None = None
    if path.suffix.lower() != ext:
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="extract-")) / f"file{ext}"
        tmp.write_bytes(path.read_bytes())
        work = tmp
    try:
        text = _extract_by_ext(work, ext)
        if ext not in TEXT_EXTENSIONS:
            text = _fix(text)
        return {
            "path": str(path),
            "filename": path.name,
            "extension": ext,
            "chars": len(text),
            "content": text,
        }
    finally:
        if tmp is not None:
            import shutil

            shutil.rmtree(tmp.parent, ignore_errors=True)


def _extract_by_ext(path: Path, ext: str) -> str:
    if ext == ".pdf":
        from .pdf.read import extract_text

        return extract_text(path)

    if ext == ".docx":
        from .docx.read import extract_text

        return extract_text(path)

    if ext in {".xlsx", ".xlsm", ".xls", ".csv", ".tsv"}:
        from .spreadsheets.read import extract_text

        return extract_text(path)

    if ext in {'.pptx', '.ppt'}:
        from .presentations.read import extract_text
        return extract_text(path)

    if ext == ".doc":
        return _extract_legacy_doc(path)

    if ext in {".odt", ".ods", ".odp"}:
        return _extract_opendocument(path)

    if ext == ".rtf":
        return _extract_rtf(path)

    # text-like
    from .text.read import extract_text
    return extract_text(path)
