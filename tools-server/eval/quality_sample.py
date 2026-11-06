"""Representative extraction check against downloaded tender files.

No OCR and no vision calls. Prints recall against an independent reader
(pypdf, python-docx, data_only openpyxl, literal OpenDocument paragraphs).

python quality_sample.py
"""
from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL.parent))
ROOT = EVAL.parent.parent
DATA = ROOT / "data"
SOURCES = [
    "tenders/zakupki_gov_ru",
    "tenders/zakupki_rosatom_ru",
    "tenders/roseltorg_ru",
    "tenders/fabrikant_ru",
    "tenders/rosatom_asutp",
    "tenders/rosatom_e2e",
    "catalogs",
]
WANTED = {".pdf", ".docx", ".xlsx", ".xls", ".doc", ".zip", ".rar", ".odt", ".ods", ".html"}
CAP = {".pdf": 20_000_000, ".docx": 8_000_000, ".xlsx": 8_000_000, ".xls": 8_000_000,
       ".doc": 8_000_000, ".zip": 30_000_000, ".rar": 30_000_000, ".odt": 8_000_000, ".ods": 8_000_000, ".html": 2_000_000}
TOKEN = re.compile(r"[0-9A-Za-zА-Яа-яЁё]{3,}")


def sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tokens(text: str) -> list[str]:
    return TOKEN.findall((text or "").lower().replace("ё", "е"))


def recall(reference: str, candidate: str) -> float | None:
    ref = tokens(reference)
    if not ref:
        return None
    got = set(tokens(candidate))
    return sum(token in got for token in ref) / len(ref)


def cyr(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum("А" <= ch.upper() <= "я" or ch.upper() == "Ё" for ch in letters) / len(letters)


def unique_files() -> list[Path]:
    seen, out = set(), []
    for source in SOURCES:
        folder = DATA / source
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if not path.is_file() or not path.stat().st_size or "_agent" in path.parts:
                continue
            digest = sha1(path)
            if digest in seen:
                continue
            seen.add(digest)
            out.append(path)
    return out


def pick(files: list[Path]) -> list[tuple[str, Path]]:
    from app.document_tool.sniff import detect_extension

    grouped = defaultdict(list)
    for path in files:
        kind = detect_extension(path) or "(unknown)"
        if kind in WANTED and path.stat().st_size <= CAP[kind]:
            grouped[kind].append(path)
    chosen = []
    for kind, paths in sorted(grouped.items()):
        paths.sort(key=lambda p: p.stat().st_size)
        usable = [p for p in paths if p.stat().st_size >= 1500] or paths
        indexes = {0, len(usable) // 2, len(usable) - 1}
        for index in sorted(indexes):
            chosen.append((kind, usable[index]))
    return chosen


def ours_text(kind: str, path: Path) -> str:
    if kind == ".pdf":
        from app.document_tool.pdf.read import extract_pages
        return "\n".join(page["text"] for page in extract_pages(path))
    if kind == ".docx":
        from app.document_tool.docx.read import extract_text
        return extract_text(path)
    if kind in {".xlsx", ".xls"}:
        from app.document_tool.spreadsheets.read import extract_text
        return extract_text(path)
    if kind == ".doc":
        from app.document_tool.legacy.read import _extract_legacy_doc
        return _extract_legacy_doc(path)
    if kind in {".odt", ".ods"}:
        from app.document_tool.legacy.read import _extract_opendocument
        return _extract_opendocument(path)
    if kind in {".zip", ".rar"}:
        from app.document_tool.archives.read import _entries
        rows = [row for row in _entries(path) if not row["directory"]]
        return "\n".join(row["name"] for row in rows)
    from app.document_tool.text.read import extract_text
    return extract_text(path)


def reference(kind: str, path: Path, ours: str) -> str:
    if kind == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if kind == ".docx":
        import docx
        document = docx.Document(str(path))
        parts = [p.text for p in document.paragraphs]
        parts.extend(cell.text for table in document.tables for row in table.rows for cell in row.cells)
        for section in document.sections:
            parts.extend(p.text for p in section.header.paragraphs)
        return "\n".join(parts)
    if kind == ".xlsx":
        from openpyxl import load_workbook
        book = load_workbook(path, data_only=True, read_only=True)
        try:
            lines = []
            for sheet in book.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    if any(cell not in (None, "") for cell in row):
                        lines.append(" | ".join("" if cell is None else str(cell) for cell in row))
            return "\n".join(lines)
        finally:
            book.close()
    if kind in {".odt", ".ods"}:
        with zipfile.ZipFile(path) as package:
            xml = package.read("content.xml").decode("utf-8", "replace")
        return "\n".join(re.findall(r"<text:p[^>]*>(.*?)</text:p>", xml))
    # DOC, XLS, archives and HTML have no second parser in this check.
    return ""


def pdf_visual_share(path: Path) -> str:
    import pymupdf
    pages = large = drawings = empty = 0
    with pymupdf.open(path) as doc:
        indexes = list(range(len(doc)))
        if len(indexes) > 12:
            indexes = [indexes[i] for i in range(0, len(indexes), max(1, len(indexes) // 12))][:12]
        for number in indexes:
            page = doc[number]
            pages += 1
            blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
            if len(" ".join(b[4] for b in blocks).strip()) < 20:
                empty += 1
            area = max(page.rect.width * page.rect.height, 1)
            images = page.get_image_info()
            if any(max(0, im["bbox"][2] - im["bbox"][0]) * max(0, im["bbox"][3] - im["bbox"][1]) >= 0.12 * area for im in images):
                large += 1
            if len(page.get_drawings()) > 15:
                drawings += 1
    return f"sampled_pages={pages} little_text={empty} large_image={large} dense_drawings={drawings}"


def main() -> None:
    files = unique_files()
    chosen = pick(files)
    print(f"unique={len(files)} sample={len(chosen)}")
    for kind, path in chosen:
        rel = path.relative_to(DATA).as_posix()
        try:
            text = ours_text(kind, path)
            ref = reference(kind, path, text)
            ours_in_ref = recall(text, ref)
            ref_in_ours = recall(ref, text)
            line = (f"{kind:6} {path.stat().st_size/1024:8.0f}KB  chars={len(text):7}  cyr={cyr(text):.2f}  "
                    f"ours_in_ref={none(ours_in_ref)}  ref_in_ours={none(ref_in_ours)}  {rel}")
            print(line)
            if kind == ".pdf":
                print("       " + pdf_visual_share(path))
            if len(text) < 400:
                print("       TEXT:", text.replace("\n", " | ")[:350])
        except Exception as exc:
            print(f"{kind:6} FAIL {type(exc).__name__}: {exc}  {rel}")


def none(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


if __name__ == "__main__":
    main()
