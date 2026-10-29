"""
Извлечение текста из файлов — по той же логике, что default-engine
Open WebUI (backend/open_webui/retrieval/loaders/main.py):
PyPDFLoader, Docx2txtLoader, TextLoader, CSV, Excel (pandas fallback), PPTX.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any

import ftfy

from ..config import ALLOWED_EXTENSIONS

log = logging.getLogger(__name__)

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".xml",
    ".csv",
    ".log",
    ".ini",
    ".yml",
    ".yaml",
    ".toml",
    ".py",
    ".js",
    ".ts",
    ".css",
    ".html",
    ".htm",
    ".rtf",
}


def _fix(text: str) -> str:
    return ftfy.fix_text(text or "").strip()


def _detect_encoding(path: Path) -> str:
    raw = path.read_bytes()[:65536]
    for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _doc_text_ole(path: Path) -> str:
    """Текст из Word 97–2003 (.doc) без внешних программ: таблица кусков (piece table) из потока
    WordDocument и потока 0Table/1Table. Нужен в desktop-версии под Windows, где нет antiword/catdoc."""
    import struct

    import olefile

    with olefile.OleFileIO(str(path)) as ole:
        word = ole.openstream("WordDocument").read()
        flags = struct.unpack_from("<H", word, 0x0A)[0]
        table_name = "1Table" if flags & 0x0200 else "0Table"
        if not ole.exists(table_name):
            raise ValueError(f"нет потока {table_name}")
        table = ole.openstream(table_name).read()
    fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x01A2)
    clx = table[fc_clx:fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:  # Prc — форматирование, пропускаем
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos >= len(clx) or clx[pos] != 0x02:
        raise ValueError("не найдена таблица кусков текста")
    size = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5:pos + 5 + size]
    count = (size - 4) // 12
    cps = struct.unpack_from(f"<{count + 1}I", plc, 0)
    parts: list[str] = []
    for i in range(count):
        fc = struct.unpack_from("<I", plc, (count + 1) * 4 + i * 8 + 2)[0]
        length = cps[i + 1] - cps[i]
        if fc & 0x40000000:  # 8-битный текст (cp1252)
            start = (fc & ~0x40000000) // 2
            parts.append(word[start:start + length].decode("cp1252", errors="replace"))
        else:
            parts.append(word[fc:fc + 2 * length].decode("utf-16-le", errors="replace"))
    text = "".join(parts)
    # Служебные символы Word: конец абзаца/ячейки → перенос строки, поля и объекты — убрать.
    text = text.replace(chr(13), chr(10)).replace(chr(7), chr(9)).replace(chr(11), chr(10)).replace(chr(12), chr(10))
    text = "".join(ch for ch in text if ch in (chr(10), chr(9)) or ch >= " ")
    return text


def _extract_legacy_doc(path: Path) -> str:
    """Extract text from legacy .doc via antiword/catdoc (installed in image), else a built-in reader."""
    import shutil
    import subprocess

    errors: list[str] = []
    for cmd in (
        ["antiword", "-m", "UTF-8.txt", str(path)],
        ["antiword", str(path)],
        ["catdoc", "-d", "utf-8", str(path)],
        ["catdoc", str(path)],
    ):
        binary = cmd[0]
        if not shutil.which(binary):
            errors.append(f"{binary} not installed")
            continue
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=60,
            )
            if proc.returncode == 0 and (proc.stdout or b"").strip():
                text = proc.stdout.decode("utf-8", errors="replace")
                if not text.strip():
                    text = proc.stdout.decode("cp1251", errors="replace")
                return text
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:200]
            errors.append(f"{' '.join(cmd)} -> rc={proc.returncode} {err}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{binary}: {e}")

    try:
        text = _doc_text_ole(path)
        if text.strip():
            return text
        errors.append("built-in reader: empty text")
    except Exception as e:  # noqa: BLE001
        errors.append(f"built-in reader: {e}")
    raise ValueError(
        "Cannot read legacy .doc ("
        + "; ".join(errors[:4])
        + "). Convert to .docx or install antiword/catdoc."
    )


def extract_file(path: Path) -> dict[str, Any]:
    """Вернуть извлечённый текст одного файла (как после upload в чат OWUI)."""
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")

    ext = path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS and ext not in TEXT_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext}")

    if ext in ARCHIVE_EXTENSIONS:
        return _extract_archive(path)

    text = _extract_by_ext(path, ext)
    text = _fix(text)
    return {
        "path": str(path),
        "filename": path.name,
        "extension": ext,
        "chars": len(text),
        "content": text,
    }


def _extract_by_ext(path: Path, ext: str) -> str:
    if ext == ".pdf":
        from langchain_community.document_loaders import PyPDFLoader

        docs = PyPDFLoader(str(path), mode="page").load()
        return "\n\n".join(d.page_content for d in docs)

    if ext == ".docx":
        from langchain_community.document_loaders import Docx2txtLoader

        docs = Docx2txtLoader(str(path)).load()
        return "\n\n".join(d.page_content for d in docs)

    if ext in {".html", ".htm"}:
        from langchain_community.document_loaders import BSHTMLLoader

        try:
            docs = BSHTMLLoader(str(path), open_encoding="utf-8").load()
        except Exception:
            docs = BSHTMLLoader(str(path), open_encoding="unicode_escape").load()
        return "\n\n".join(d.page_content for d in docs)

    if ext == ".csv":
        enc = _detect_encoding(path)
        import pandas as pd

        # Как при «простом» чтении таблицы: сохранить строки целиком
        for sep in (";", ",", "\t"):
            try:
                df = pd.read_csv(path, encoding=enc, sep=sep)
                if df.shape[1] > 1:
                    break
            except Exception:
                continue
        else:
            df = pd.read_csv(path, encoding=enc, sep=None, engine="python")
        summary = (
            f"Table: {len(df) + 1} rows incl. header; {len(df)} data rows; "
            f"{len(df.columns)} columns: {', '.join(map(str, df.columns))}."
        )
        return f"{summary}\n\n{df.to_string(index=False)}"

    if ext in {".xlsx", ".xls"}:
        import pandas as pd

        parts: list[str] = []
        engine = "xlrd" if ext == ".xls" else "openpyxl"
        try:
            xls = pd.ExcelFile(path, engine=engine)
        except Exception:
            # fallback: let pandas choose
            xls = pd.ExcelFile(path)
        # Закрываем файл явно: открытый дескриптор не даёт удалить временную папку распакованного архива.
        with xls:
            for sheet in xls.sheet_names:
                df = pd.read_excel(xls, sheet_name=sheet)
                parts.append(f"Sheet: {sheet}\n{df.to_string(index=False)}")
        return "\n\n".join(parts)

    if ext in {".pptx", ".ppt"}:
        try:
            from pptx import Presentation

            prs = Presentation(str(path))
            parts = []
            for i, slide in enumerate(prs.slides, 1):
                texts = [
                    shape.text_frame.text
                    for shape in slide.shapes
                    if shape.has_text_frame and shape.text_frame.text.strip()
                ]
                if texts:
                    parts.append(f"Slide {i}:\n" + "\n".join(texts))
            return "\n\n".join(parts)
        except Exception as e:
            raise ValueError(f"Cannot read PowerPoint: {e}") from e

    if ext == ".doc":
        return _extract_legacy_doc(path)

    # text-like
    enc = _detect_encoding(path)
    return path.read_text(encoding=enc, errors="replace")


ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z"}
MAX_ARCHIVE_DEPTH = 3
MAX_ARCHIVE_FILES = 200


def _zip_name(info: zipfile.ZipInfo) -> str:
    """Имена в zip без флага UTF-8 пишутся в cp866 (Windows), а zipfile читает их как cp437."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except UnicodeError:
        return info.filename


def _unpack(path: Path, dest: Path) -> None:
    if path.suffix.lower() == ".zip" and zipfile.is_zipfile(path):  # «.zip» бывает переименованным rar — его отдаём tar
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                name = _zip_name(info)
                target = (dest / name).resolve()
                if info.is_dir() or not str(target).startswith(str(dest.resolve())):
                    continue  # каталоги создаются сами; пути с ../ не пускаем
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))
        return
    # rar и 7z: tar.exe из Windows 10+ (libarchive) читает оба формата без сторонних программ.
    import shutil
    import subprocess

    tar = shutil.which("tar", path=r"C:\Windows\System32") or shutil.which("bsdtar") or "tar"
    p = subprocess.run([tar, "-xf", str(path), "-C", str(dest)], capture_output=True, timeout=300)
    if p.returncode != 0 and not any(dest.rglob("*")):
        err = p.stderr.decode("utf-8", "replace") or p.stderr.decode("cp866", "replace")
        raise ValueError(f"Cannot unpack {path.suffix}: {err.strip()[:200]}")


def _extract_archive(path: Path, depth: int = 0) -> dict[str, Any]:
    """Распаковать zip/rar/7z во временную папку и извлечь текст вложенных файлов, вложенные архивы тоже."""
    import tempfile

    parts: list[str] = []
    files_meta: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="arc-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        _unpack(path, root)
        files = sorted(p for p in root.rglob("*") if p.is_file())
        for f in files[:MAX_ARCHIVE_FILES]:
            name = str(f.relative_to(root)).replace("\\", "/")
            if "__MACOSX" in name or f.name.startswith("."):
                continue
            ext = f.suffix.lower()
            try:
                if ext in ARCHIVE_EXTENSIONS:
                    if depth + 1 >= MAX_ARCHIVE_DEPTH:
                        files_meta.append({"filename": name, "skipped": True, "reason": "archive nested too deep"})
                        continue
                    inner = _extract_archive(f, depth + 1)
                    extracted = inner["content"]
                    files_meta.extend({**m, "filename": f"{name}/{m['filename']}"} for m in inner["zip_files"])
                elif ext not in ALLOWED_EXTENSIONS and ext not in TEXT_EXTENSIONS:
                    files_meta.append({"filename": name, "skipped": True, "reason": f"unsupported {ext}"})
                    continue
                else:
                    extracted = _fix(_extract_by_ext(f, ext))
                    files_meta.append({"filename": name, "chars": len(extracted), "skipped": False})
                parts.append(f"===== {name} =====\n{extracted}")
            except Exception as e:
                files_meta.append({"filename": name, "skipped": True, "reason": str(e)[:200]})
        if len(files) > MAX_ARCHIVE_FILES:
            files_meta.append({"filename": "...", "skipped": True, "reason": f"only first {MAX_ARCHIVE_FILES} files"})

    content = _fix("\n\n".join(parts))
    return {
        "path": str(path),
        "filename": path.name,
        "extension": path.suffix.lower(),
        "chars": len(content),
        "content": content,
        "zip_files": files_meta,
    }
