"""Безопасная работа с папками внутри DATA_ROOT."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import ALLOWED_EXTENSIONS, DATA_ROOT
from .extract import TEXT_EXTENSIONS, extract_file


def resolve_under_data(relative_or_abs: str) -> Path:
    """
    Путь относительно DATA_ROOT (например tenders/foo.pdf)
    или абсолютный, но только внутри DATA_ROOT.
    """
    raw = (relative_or_abs or "").strip().replace("\\", "/")
    if not raw or raw in {".", "./", "/"}:
        return DATA_ROOT

    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = DATA_ROOT / candidate
    resolved = candidate.resolve()

    try:
        resolved.relative_to(DATA_ROOT)
    except ValueError as e:
        raise PermissionError(
            f"Path outside DATA_ROOT ({DATA_ROOT}): {relative_or_abs}"
        ) from e
    return resolved


def list_directory(path: str = "", recursive: bool = True) -> dict[str, Any]:
    root = resolve_under_data(path)
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {path or '.'}")
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    entries: list[dict[str, Any]] = []
    iterator = root.rglob("*") if recursive else root.iterdir()
    for p in sorted(iterator):
        if p.name.startswith(".") or p.name.startswith(".__"):
            continue
        rel = str(p.relative_to(DATA_ROOT)).replace("\\", "/")
        if p.is_dir():
            entries.append({"path": rel, "type": "dir", "name": p.name})
        elif p.is_file():
            ext = p.suffix.lower()
            supported = ext in ALLOWED_EXTENSIONS or ext in TEXT_EXTENSIONS
            entries.append(
                {
                    "path": rel,
                    "type": "file",
                    "name": p.name,
                    "size_bytes": p.stat().st_size,
                    "extension": ext,
                    "supported": supported,
                }
            )

    return {
        "data_root": str(DATA_ROOT),
        "path": str(root.relative_to(DATA_ROOT)).replace("\\", "/")
        if root != DATA_ROOT
        else ".",
        "count": len(entries),
        "entries": entries,
    }


def read_document(path: str, max_chars: int) -> dict[str, Any]:
    file_path = resolve_under_data(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not file_path.is_file():
        raise IsADirectoryError(f"Expected file, got directory: {path}")

    rel = str(file_path.relative_to(DATA_ROOT)).replace("\\", "/")
    ext = file_path.suffix.lower()
    from .. import doc_index

    if ext == ".pdf" or ext in doc_index.MARKITDOWN_EXTENSIONS:
        # Тот же разбор, что у постраничного индекса: OCR сканов и битых шрифтов, таблицы DOCX в Markdown.
        # pypdf на скачанных тендерах отдавал пустыми 21 PDF из 95.
        idx = doc_index.get_index(rel)
        pages = [idx.text(n, run_ocr=True)[0] for n in range(1, len(idx.pages) + 1)]
        text = "\n\n".join(f"[стр. {n}]\n{t}" for n, t in enumerate(pages, 1)) if ext == ".pdf" else "\n\n".join(pages)
        result = {"path": str(file_path), "filename": file_path.name, "extension": ext, "pages": len(pages), "chars": len(text), "content": text}
    else:
        result = extract_file(file_path)
    content = result["content"]
    truncated = False
    if max_chars > 0 and len(content) > max_chars:
        content = content[:max_chars] + "\n\n[... truncated: use search_document / read_pages for the rest ...]"
        truncated = True

    return {**result, "path": rel, "content": content, "truncated": truncated}


def read_folder_documents(
    path: str,
    max_files: int,
    max_chars_per_file: int,
    recursive: bool = True,
    max_total_chars: int = 150_000,
) -> dict[str, Any]:
    root = resolve_under_data(path)
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {path or '.'}")
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    files: list[Path] = []
    iterator = root.rglob("*") if recursive else root.iterdir()
    for p in sorted(iterator):
        if not p.is_file() or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext in ALLOWED_EXTENSIONS or ext in TEXT_EXTENSIONS:
            files.append(p)

    # Сначала маленькие файлы: так целиком попадает больше документов, а оглавлениями идут большие.
    selected = sorted(files[: max(1, max_files)], key=lambda p: p.stat().st_size)
    documents: list[dict[str, Any]] = []
    outlines: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    used = 0

    # Гибрид: пока папка помещается в бюджет, отдаём файлы целиком (быстро и дёшево); дальше — только оглавления,
    # остальное модель дочитывает поиском и страницами. Так маленькая закупка читается за один вызов,
    # а большая не переполняет контекст.
    for p in selected:
        rel = str(p.relative_to(DATA_ROOT)).replace("\\", "/")
        try:
            if used < max_total_chars:
                doc = read_document(rel, min(max_chars_per_file, max_total_chars - used))
                used += len(doc["content"])
                documents.append(doc)
            else:
                outlines.append(_outline(rel))
        except Exception as e:
            errors.append({"path": rel, "error": str(e)})

    return {
        "path": str(root.relative_to(DATA_ROOT)).replace("\\", "/")
        if root != DATA_ROOT
        else ".",
        "total_supported_files": len(files),
        "returned_files": len(documents),
        "documents": documents,
        "not_read_fully": outlines,
        "errors": errors,
        "note": (
            "Full text of the first files fits the budget. Files in not_read_fully and truncated ones are "
            "given as outlines: read them with search_document / read_pages (PDF, DOCX) or "
            "describe_table / query_table (Excel)."
            if outlines or any(d.get("truncated") for d in documents)
            else "All files are given in full."
        ),
    }


def _outline(rel: str) -> dict[str, Any]:
    if rel.lower().endswith((".xlsx", ".xls", ".csv")):
        from ..table_tool import describe_table

        d = describe_table(rel)
        return {"path": rel, "sheets": [{"sheet": s["sheet"], "rows": s["data_rows"], "columns": [c["header"] or c["column"] for c in s["columns"]][:30]} for s in d["details"]]}
    from .. import doc_index

    info = doc_index.document_info(rel)
    titles = [f"{o['page']}: {o['title']}" for o in info["outline"] if o["title"]]
    return {"path": rel, "pages": info["pages"], "chars": info["chars"], "outline": titles[:40]}
