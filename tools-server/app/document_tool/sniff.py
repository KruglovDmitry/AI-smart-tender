"""Определение типа файла по содержимому (magic bytes), когда расширения нет или оно ложное."""

from __future__ import annotations

import io
from pathlib import Path
from typing import BinaryIO

# Короткие суффиксы, которым можно доверять в имени (не «I. Общие сведения…»).
KNOWN_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
    ".zip",
    ".rar",
    ".7z",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
}

_CONTENT_TYPE = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
    "application/zip": ".zip",
    "application/x-rar-compressed": ".rar",
    "application/vnd.rar": ".rar",
    "application/x-7z-compressed": ".7z",
    "application/vnd.oasis.opendocument.text": ".odt",
}


def known_suffix(name: str) -> str:
    """Расширение из имени, только если оно из списка известных (иначе '' — имя вроде «I. …»)."""
    suffix = Path(name or "").suffix.lower()
    return suffix if suffix in KNOWN_EXTENSIONS else ""


# Форматы, где содержимое однозначно определяет тип: при расхождении с расширением верим содержимому
# («Скачать все документы.docx», который на деле zip; .doc, который на деле docx).
CONTENT_TYPED = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods", ".odp", ".rtf",
                 ".zip", ".rar", ".7z"}


def _sniff(head: bytes, open_file) -> str:
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.lstrip().startswith(b"{\\rt"):
        return ".rtf"
    if head.startswith(b"Rar!"):
        return ".rar"
    if head.startswith(b"7z\xbc\xaf"):
        return ".7z"
    if head.startswith(b"PK\x03\x04"):
        return _sniff_zip(open_file())
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return _sniff_ole(open_file())
    return ""


def sniff_bytes(data: bytes, content_type: str | None = None) -> str:
    """Вернуть расширение с точкой (`.pdf`, `.docx`…) или пустую строку."""
    if not data:
        return ""
    found = _sniff(data[:8], lambda: io.BytesIO(data))
    if found:
        return found
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    return _CONTENT_TYPE.get(mime, "")


def sniff_path(path: Path) -> str:
    """Как sniff_bytes, но zip и OLE открываются с диска: их оглавление может лежать в конце большого файла."""
    try:
        with path.open("rb") as f:
            head = f.read(8)
        return _sniff(head, lambda: path.open("rb"))
    except OSError:
        return ""


def detect_extension(path: Path, name: str | None = None) -> str:
    """Настоящее расширение файла: из имени, если содержимое ему не противоречит, иначе по содержимому."""
    claimed = known_suffix(name or path.name)
    sniffed = sniff_path(path)
    if sniffed and (not claimed or (claimed in CONTENT_TYPED and sniffed != claimed)):
        return sniffed
    return claimed or Path(name or path.name).suffix.lower()


def _false_suffix(name: str) -> str:
    """Короткий суффикс вроде `.bin`/`.tmp`, но не из известных; «I. Общие…» не трогаем."""
    suffix = Path(name or "").suffix
    body = suffix[1:] if suffix.startswith(".") else ""
    if not body or len(body) > 8 or " " in body or not body.replace("_", "").isalnum():
        return ""
    return "" if f".{body.lower()}" in KNOWN_EXTENSIONS else suffix


def ensure_extension(name: str, data: bytes, content_type: str | None = None) -> str:
    """Дописать или заменить расширение, если в имени его нет / оно неизвестно."""
    cleaned = (name or "file").strip() or "file"
    if known_suffix(cleaned):
        return cleaned
    sniffed = sniff_bytes(data, content_type)
    if not sniffed:
        return cleaned
    false = _false_suffix(cleaned)
    stem = (cleaned[: -len(false)] if false else cleaned) or cleaned
    if stem.lower().endswith(sniffed):
        return stem
    return f"{stem}{sniffed}"


def _sniff_zip(f: BinaryIO) -> str:
    import zipfile

    with f:
        try:
            zf = zipfile.ZipFile(f)
            names = zf.namelist()
        except Exception:
            return ".zip"
        if any(n.startswith("word/") for n in names):
            return ".docx"
        if any(n.startswith("xl/") for n in names):
            return ".xlsx"
        if any(n.startswith("ppt/") for n in names):
            return ".pptx"
        if "content.xml" in names:
            # ODT/ODS/ODP — различаем по mimetype, если есть.
            try:
                mt = zf.read("mimetype").decode("ascii", errors="ignore") if "mimetype" in names else ""
            except Exception:
                mt = ""
            if "spreadsheet" in mt:
                return ".ods"
            if "presentation" in mt:
                return ".odp"
            return ".odt"
        return ".zip"


def _sniff_ole(f: BinaryIO) -> str:
    """Word / Excel / PowerPoint 97–2003 по потокам составного документа; прочее OLE (.msg и т.п.) — ''."""
    import olefile

    with f:
        try:
            with olefile.OleFileIO(f) as ole:
                if ole.exists("WordDocument"):
                    return ".doc"
                if ole.exists("Workbook") or ole.exists("Book"):
                    return ".xls"
                if ole.exists("PowerPoint Document"):
                    return ".ppt"
        except Exception:
            pass
    return ""
