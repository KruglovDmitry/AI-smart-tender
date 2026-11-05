"""Внешний загрузчик документов для «Знаний» Open WebUI (настройка «External document loader»).

Open WebUI шлёт файл PUT {url}/process (тело — байты файла, имя в X-Filename) и ждёт список
{page_content, metadata}. Отдаём файл постранично тем же разбором, что и инструменты: OCR битых
страниц и сканов, Markdown для DOCX/PPTX/HTML, номер страницы в метаданных. Чтобы гибридный поиск
находил артикул в любом написании, к странице дописываются коды без разделителей.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ...document_index import _build_pages
from ...document_tool.pdf import ocr, read as pdf
import hashlib

log = logging.getLogger(__name__)

# 55.32.9.024.0000, 40.52-8.230, 3SK1 122-1AB40 — коды из групп цифр/букв с точками, дефисами, слэшами.
_CODE = re.compile(r"\b[0-9A-Z]{1,6}(?:[.\-/][0-9A-Z]{1,6}){2,}\b")
# Таблицы ищем только в небольших файлах: find_tables ≈2 с на страницу.
TABLES_MAX_PAGES = 40


def _codes_line(text: str) -> str:
    codes = []
    for m in _CODE.finditer(text.upper()):
        c = m.group(0)
        if any(ch.isdigit() for ch in c):
            flat = re.sub(r"[.\-/]", "", c)
            if flat != c and flat not in codes:
                codes.append(flat)
    return ("\n\nКоды без разделителей: " + " ".join(codes[:200])) if codes else ""


def process_upload(data: bytes, filename: str, content_type: str | None = None, *, use_ocr: bool = True, include_tables: bool = True) -> list[dict[str, Any]]:
    from ...document_index import DocIndex, INDEX_ROOT, INDEX_VERSION
    from ...document_tool.sniff import detect_extension, ensure_extension

    name = unquote(filename or "document")
    # Имя с площадки часто без расширения («I. Общие сведения») или с ложным — тип берём из байтов / Content-Type.
    resolved = ensure_extension(name, data, content_type)
    with tempfile.TemporaryDirectory(prefix="owui-") as tmp:
        raw = Path(tmp) / "upload.bin"
        raw.write_bytes(data)
        suffix = detect_extension(raw, resolved) or ".bin"
        path = raw if suffix == ".bin" else raw.rename(raw.with_suffix(suffix))
        try:
            pages, backend = _build_pages(path)
        except Exception as e:
            # Свой разбор упал (битый файл, ошибка библиотеки) — отдаём текст прежним простым способом.
            # Неподдерживаемый формат пробрасываем: Open WebUI тогда разберёт файл сам.
            log.warning("OWUI loader: page parsing failed for %s (%s), falling back to extract_file", name, e)
            from ...document_tool.extract import extract_file

            pages = [pdf.page_record(extract_file(path)["content"], images=0)]
            backend = "extract_file"
        is_pdf = suffix == ".pdf" and backend != "extract_file"
        digest = hashlib.sha256(data).hexdigest()
        index = DocIndex(path, INDEX_ROOT / f"uploads-v{INDEX_VERSION}" / digest,
                         {"file_type": ".pdf" if is_pdf else "", "backend": backend},
                         pages, rel=f"uploads/{digest}")
        docs = []
        for n, p in enumerate(pages, 1):
            # Upload policy stays text/OCR only; no Qwen-VL or automatic full-reader calls.
            run_ocr = use_ocr and index.needs_ocr(n) and ocr.available()
            page = index.read_page(n, run_ocr=run_ocr, include_structure=False,
                include_tables=include_tables and is_pdf and len(pages) <= TABLES_MAX_PAGES,
                tables_on_ocr=False)
            text, source = page["text"], page["source"]
            if page["tables"]:
                text += "\n\n" + "\n\n".join(page["tables"])
            if not text.strip():
                continue
            meta = {"name": name, "source": name, "total_pages": len(pages), "extraction": f"{backend}/{source}"}
            if page["warnings"]:
                meta["warnings"] = "; ".join(page["warnings"])
            if is_pdf:
                meta.update(page=n - 1, page_label=str(n))  # как у PyPDFLoader: page с нуля, page_label с единицы
            else:
                meta.update(part=n)
            docs.append({"page_content": text + _codes_line(text), "metadata": meta})
    log.info("OWUI loader: %s -> %d pages (%s)", name, len(docs), backend)
    return docs or [{"page_content": "", "metadata": {"name": name, "source": name}}]
