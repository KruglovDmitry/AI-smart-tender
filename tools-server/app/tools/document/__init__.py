"""Безопасная работа с папками внутри DATA_ROOT."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...config import ALLOWED_EXTENSIONS, DATA_ROOT
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

    result = extract_file(file_path)
    content = result["content"]
    truncated = False
    if max_chars > 0 and len(content) > max_chars:
        content = content[:max_chars] + "\n\n[... truncated ...]"
        truncated = True

    rel = str(file_path.relative_to(DATA_ROOT)).replace("\\", "/")
    out = {
        **result,
        "path": rel,
        "content": content,
        "truncated": truncated,
        "note": (
            "Text extracted the same way as Open WebUI default document upload "
            "(PyPDF / Docx2txt / Text / Excel / etc.), then passed as context."
        ),
    }
    return out


def read_folder_documents(
    path: str,
    max_files: int,
    max_chars_per_file: int,
    recursive: bool = True,
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

    selected = files[: max(1, max_files)]
    documents: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for p in selected:
        rel = str(p.relative_to(DATA_ROOT)).replace("\\", "/")
        try:
            documents.append(read_document(rel, max_chars_per_file))
        except Exception as e:
            errors.append({"path": rel, "error": str(e)})

    return {
        "path": str(root.relative_to(DATA_ROOT)).replace("\\", "/")
        if root != DATA_ROOT
        else ".",
        "total_supported_files": len(files),
        "returned_files": len(documents),
        "documents": documents,
        "errors": errors,
        "note": (
            "Each file is extracted like an Open WebUI chat attachment "
            "(full text into context, with per-file char limit)."
        ),
    }
