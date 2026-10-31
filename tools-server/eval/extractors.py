"""Извлекатели для сравнения. Каждый возвращает список документов [{"text", "meta"}] — как загрузчик Open WebUI.

ours      — наш внешний загрузчик (/process tools-server): постранично, OCR, таблицы, коды без разделителей.
builtin   — то, что Open WebUI делает сам без внешнего загрузчика в нашей сборке (pypdf, docx2txt, pandas;
            unstructured в сборке нет, поэтому doc/rtf/odt/архивы не разбираются).
markitdown — MarkItDown целиком (pdfminer для PDF).
docling   — Docling (IBM): разметка макета и таблиц, экспорт в Markdown.
"""

from __future__ import annotations

from pathlib import Path

import common  # noqa: F401  (sys.path, DATA_ROOT)


def ours(path: Path, ocr: bool = True, tables: bool = True, name: str | None = None) -> list[dict]:
    from app.doc_index import ocr as ocr_mod, owui_loader, pdf as pdf_mod

    saved = ocr_mod.available, pdf_mod.page_tables
    if not ocr:
        ocr_mod.available = lambda: False
    if not tables:
        pdf_mod.page_tables = lambda p, n: []
    try:
        docs = owui_loader.process_upload(path.read_bytes(), name or path.name)
    finally:
        ocr_mod.available, pdf_mod.page_tables = saved
    return [{"text": d["page_content"], "meta": d["metadata"]} for d in docs if d["page_content"].strip()]


def builtin(path: Path, name: str | None = None) -> list[dict]:
    ext = Path(name or path.name).suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        r = PdfReader(str(path))
        return [{"text": p.extract_text() or "", "meta": {"page": i}} for i, p in enumerate(r.pages)]
    if ext == ".docx":
        import docx2txt

        return [{"text": docx2txt.process(str(path)), "meta": {}}]
    if ext in (".xlsx", ".xls"):
        import pandas as pd

        sheets = pd.read_excel(path, sheet_name=None)
        return [{"text": f"Sheet: {n}\n" + df.to_string(index=False), "meta": {"sheet": n}} for n, df in sheets.items()]
    if ext in (".txt", ".md", ".csv"):
        return [{"text": path.read_text(encoding="utf-8", errors="replace"), "meta": {}}]
    raise ValueError(f"builtin OWUI loader: {ext or 'no extension'} requires unstructured (absent in build)")


def markitdown(path: Path, name: str | None = None) -> list[dict]:
    from markitdown import MarkItDown

    return [{"text": MarkItDown(enable_plugins=False).convert(str(path)).text_content or "", "meta": {}}]


_docling = None


def docling(path: Path, name: str | None = None) -> list[dict]:
    global _docling
    if _docling is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        opts = PdfPipelineOptions(do_ocr=True, do_table_structure=True)
        opts.ocr_options = RapidOcrOptions(lang=["russian"])
        _docling = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    res = _docling.convert(str(path))
    doc = res.document
    pages = sorted(doc.pages) if doc.pages else []
    if len(pages) > 1:
        out = []
        for p in pages:
            md = doc.export_to_markdown(page_no=p)
            if md.strip():
                out.append({"text": md, "meta": {"page": p - 1}})
        return out
    return [{"text": doc.export_to_markdown(), "meta": {}}]


EXTRACTORS = {"ours": ours, "builtin": builtin, "markitdown": markitdown, "docling": docling}
