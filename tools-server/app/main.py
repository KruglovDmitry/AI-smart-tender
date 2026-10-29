"""OpenAPI Tool Server for Open WebUI: documents, fetch URL, Excel export."""

from __future__ import annotations

import logging
import sys


def _force_utf8_stdio() -> None:
    """Windows-консоль часто в cp1251/cp866 — логи с кириллицей превращаются в кракозябры."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_force_utf8_stdio()

from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config
from . import doc_index, table_tool
from .table_tool import jobs as table_jobs
from .document_tool import list_directory, read_document, read_folder_documents
from .excel_tool import resolve_export_file, write_excel
from .web_tool import fetch_page

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
)
logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)

app = FastAPI(
    title="Tender Tools API",
    version="1.5.0",
    description="Server documents, URL fetch, and Excel export.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    from smart_license.web import install as install_license
except ImportError:  # Docker и разработка: пакета лицензии нет, а в собранной программе он обязателен
    if getattr(sys, "frozen", False):
        raise
else:
    install_license(app, "documents")


class ReadDocumentBody(BaseModel):
    path: str = Field(
        ...,
        description=(
            "Relative path under server data root only, e.g. 'tenders/spec.pdf' "
            "or 'catalogs/prices.xlsx'. Do NOT use for chat attachments — "
            "those are already in the conversation context."
        ),
    )
    max_chars: int = Field(
        default=config.DEFAULT_MAX_CHARS,
        description="Max characters of extracted text to return.",
    )


class ReadFolderBody(BaseModel):
    path: str = Field(
        ...,
        description="Relative folder under data root, e.g. 'tenders/auction-42'.",
    )
    recursive: bool = Field(True, description="Scan subfolders.")
    max_files: int = Field(
        default=config.DEFAULT_MAX_FILES,
        description="Max number of documents to extract.",
    )
    max_chars_per_file: int = Field(
        default=config.DEFAULT_MAX_CHARS,
        description="Max characters per file.",
    )


class FetchUrlBody(BaseModel):
    url: str = Field(
        ...,
        description=(
            "Public http(s) URL exactly as provided by the user "
            "(do not invent paths like /sitemap.xml). "
            "Use as a complement to chat attachments when a link is present."
        ),
    )
    max_chars: int = Field(
        default=config.DEFAULT_MAX_CHARS,
        description="Max characters of extracted page text.",
    )


class ExcelSheetBody(BaseModel):
    name: str = Field(..., description="Sheet tab name, e.g. 'Позиции'")
    headers: list[str] = Field(
        ...,
        description="Column headers in order",
        min_length=1,
    )
    rows: list[list[Any]] = Field(
        default_factory=list,
        description="Data rows; each row is a list of cell values aligned to headers",
    )


class WriteExcelBody(BaseModel):
    filename: str = Field(
        ...,
        description="Output file name, e.g. 'tz_positions.xlsx' (saved under exports/)",
    )
    sheets: list[ExcelSheetBody] = Field(
        ...,
        description="One or more sheets to write into the workbook",
        min_length=1,
    )


class DocumentInfoBody(BaseModel):
    path: str = Field(..., description="Relative path under data root, e.g. 'catalogs/Finder 23-24.pdf'.")
    page_from: int = Field(1, description="First page of the outline.")
    page_to: int = Field(0, description="Last page of the outline (0 = all; prefer ranges of ~100 pages for big files).")


class SearchDocumentBody(BaseModel):
    path: str = Field(..., description="Relative path under data root.")
    query: str = Field(
        ...,
        description=(
            "Article/part number (55.32.9.024.0000 and 553290240000 match the same) or words to find. "
            "For codes the search falls back to the longest printed prefix (series)."
        ),
    )
    max_results: int = Field(10, description="Max pages to return.")
    mode: str = Field("auto", description="auto | code | text")


class ReadPagesBody(BaseModel):
    path: str = Field(..., description="Relative path under data root.")
    pages: str = Field(..., description="Pages to read: '94', '94-96' or '12,40,41' (max 10 per call).")
    include_tables: bool = Field(True, description="Also return tables of these pages as Markdown.")
    max_chars: int = Field(60000, description="Max characters in the response.")


class ViewPageBody(BaseModel):
    path: str = Field(..., description="Relative path under data root to a PDF.")
    page: int = Field(..., description="Page number.")
    question: str = Field(
        "",
        description="What to look at, e.g. 'какие клеммы на схеме подключения' (empty = describe the page).",
    )


class DescribeTableBody(BaseModel):
    path: str = Field(..., description="Relative path under data root to .xlsx/.xls/.csv.")
    sheet: str | None = Field(None, description="Sheet name; empty = all sheets.")
    header_row: int | None = Field(None, description="Header row number if auto-detection is wrong.")


class TableFilter(BaseModel):
    column: str = Field(..., description="Column letter (B), number or header text.")
    op: str = Field("eq", description="eq | ne | contains | not_contains | startswith | in | empty | not_empty | gt | lt")
    value: Any = Field(None, description="Value to compare; for 'in' a list or 'a|b|c'.")


class QueryTableBody(BaseModel):
    path: str = Field(..., description="Relative path under data root to .xlsx/.xls/.csv.")
    sheet: str | None = Field(None, description="Sheet name; empty = first sheet.")
    header_row: int | None = Field(None, description="Header row number; empty = auto.")
    data_start_row: int | None = Field(None, description="First data row when service rows follow the header.")
    columns: list[str] | None = Field(None, description="Columns to return (letters or headers); empty = all.")
    filters: list[TableFilter] = Field(default_factory=list, description="All filters must match.")
    offset: int = Field(0, description="Skip this many matched rows (paging).")
    limit: int = Field(50, description="Rows to return, max 200.")
    distinct: str | None = Field(None, description="Instead of rows, count distinct values of this column.")


class TableUpdate(BaseModel):
    row: int | None = Field(None, description="Sheet row number to write.")
    key: str | None = Field(None, description="Value of key_column identifying the row(s), instead of row.")
    values: dict[str, Any] = Field(..., description="{column letter or header: value}")


class FillTableBody(BaseModel):
    path: str = Field(
        ...,
        description="Source .xlsx under data root; or a file under exports/ returned earlier to keep filling it.",
    )
    updates: list[TableUpdate] = Field(..., description="Cells to write.")
    sheet: str | None = Field(None, description="Sheet name; empty = first sheet.")
    header_row: int | None = Field(None, description="Header row number; empty = auto.")
    key_column: str | None = Field(None, description="Column used to find rows by `key` (e.g. article column).")
    allowed_values: dict[str, list[str]] | None = Field(
        None, description="{column: [allowed values]}: values outside the list are rejected, not written."
    )


class JobOutput(BaseModel):
    description: str = Field("", description="What to put into this column and how to decide it.")
    allowed: list[str] | None = Field(None, description="Allowed values (e.g. from the template's reference sheet).")


class StartTableJobBody(BaseModel):
    path: str = Field(..., description="Source .xlsx under data root (a copy is filled, the source is untouched).")
    instruction: str = Field(..., description="Task for every row, in plain words.")
    input_columns: list[str] = Field(..., description="Columns the model sees for each row (letters or headers).")
    outputs: dict[str, JobOutput] = Field(..., description="{column to fill: {description, allowed}}")
    sheet: str | None = Field(None, description="Sheet name; empty = first sheet.")
    header_row: int | None = Field(None, description="Header row number; empty = auto.")
    data_start_row: int | None = Field(None, description="First data row when service rows follow the header.")
    filters: list[TableFilter] = Field(default_factory=list, description="Process only rows matching all filters.")
    document: str | None = Field(None, description="Reference document under data root (catalog) to look rows up in.")
    lookup_column: str | None = Field(None, description="Column whose value (article) is searched in `document`.")
    batch_size: int = Field(30, description="Rows per model call (1-60).")
    workers: int = Field(3, description="Parallel model calls (1-6).")
    max_rows: int = Field(0, description="Process only the first N matching rows (0 = all); use 20-50 for a trial run.")
    example_rows: list[int] | None = Field(
        None,
        description="Rows the user filled by hand to show the style. Empty = up to 6 rows whose outputs are already filled.",
    )
    skip_filled: bool = Field(True, description="Do not reprocess rows whose output columns are all filled.")


def _http_errors(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (ValueError, KeyError, IsADirectoryError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except Exception as e:
        logging.getLogger(__name__).exception("tool failed")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e


def _public_base(request: Request) -> str:
    configured = config.TOOLS_PUBLIC_BASE_URL
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


@app.get("/health", summary="Health check", include_in_schema=False)
def health():
    return {"status": "ok", "data_root": str(config.DATA_ROOT)}


@app.get(
    "/list_documents",
    summary="List documents on the server",
    description=(
        "List files and folders under the server data directory "
        "(tenders/, catalogs/, uploads/, exports/). "
        "Not for chat attachments — those are already in the model context. "
        "Use when the user refers to a server folder/path."
    ),
)
def list_documents(
    path: str = Query(
        "",
        description="Relative path under data root. Empty = data root.",
    ),
    recursive: bool = Query(True, description="Include nested files."),
):
    try:
        return list_directory(path, recursive=recursive)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except NotADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


@app.post(
    "/read_document",
    summary="Read one document from the server",
    description=(
        "Extract text from a file on the server data volume "
        "(PDF/DOCX/TXT/CSV/XLSX/ZIP/...). "
        "Do NOT call this for files attached in the chat — use chat context instead. "
        "Use only for paths under tenders/, catalogs/, uploads/ on the server. "
        "For big PDFs use document_info/search_document/read_pages; for spreadsheets describe_table/query_table."
    ),
)
def api_read_document(body: ReadDocumentBody):
    try:
        return read_document(body.path, max_chars=body.max_chars)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (IsADirectoryError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extract failed: {e}") from e


@app.post(
    "/read_folder",
    summary="Read all documents from a server folder",
    description=(
        "Extract text from every supported file in a server data folder "
        "(tenders/, catalogs/, ...). Not a substitute for chat attachments."
    ),
)
def api_read_folder(body: ReadFolderBody):
    try:
        return read_folder_documents(
            body.path,
            max_files=body.max_files,
            max_chars_per_file=body.max_chars_per_file,
            recursive=body.recursive,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except NotADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


@app.post(
    "/document_info",
    summary="Outline of a large document",
    description=(
        "Page count and a one-line title per page for a PDF/DOCX on the server, plus pages that needed OCR. "
        "Use for big files (catalogs, tender documentation) instead of read_document; then search_document "
        "and read_pages."
    ),
)
def api_document_info(body: DocumentInfoBody):
    return _http_errors(doc_index.document_info, body.path, body.page_from, body.page_to)


@app.post(
    "/search_document",
    summary="Find pages in a document by article number or words",
    description=(
        "Search a server document page by page. Article numbers are matched ignoring dots/spaces; if the exact "
        "code is not printed, returns pages of its series (match=prefix): catalogs often build codes from "
        "option tables, read those pages to decode it."
    ),
)
def api_search_document(body: SearchDocumentBody):
    return _http_errors(doc_index.search_document, body.path, body.query, body.max_results, body.mode)


@app.post(
    "/read_pages",
    summary="Read selected pages of a document",
    description="Text and tables (Markdown) of chosen pages; broken-font or scanned pages are OCR-ed.",
)
def api_read_pages(body: ReadPagesBody):
    return _http_errors(doc_index.read_pages, body.path, body.pages, body.include_tables, body.max_chars)


@app.post(
    "/view_page",
    summary="Look at a document page as an image",
    description=(
        "Render a PDF page and ask a vision model a question about it: diagrams, drawings, photos, "
        "tables printed as images. Use when read_pages text is not enough to decide. Answers are cached."
    ),
)
def api_view_page(body: ViewPageBody):
    from .doc_index import vision

    return _http_errors(vision.view_page, body.path, body.page, body.question)


@app.post(
    "/describe_table",
    summary="Structure of a spreadsheet",
    description=(
        "Sheets, header row, columns with fill counts and example/distinct values, first rows. "
        "Call first for any Excel/CSV on the server instead of reading it whole: big tables do not fit "
        "into the model context."
    ),
)
def api_describe_table(body: DescribeTableBody):
    return _http_errors(table_tool.describe_table, body.path, body.sheet, body.header_row)


@app.post(
    "/query_table",
    summary="Get spreadsheet rows by filter",
    description="Rows matching filters with paging (max 200 per call), or distinct value counts of a column.",
)
def api_query_table(body: QueryTableBody):
    return _http_errors(
        table_tool.query_table,
        body.path,
        body.sheet,
        body.header_row,
        body.data_start_row,
        body.columns,
        [f.model_dump() for f in body.filters],
        body.offset,
        body.limit,
        body.distinct,
    )


@app.post(
    "/fill_table",
    summary="Write values into a copy of a spreadsheet",
    description=(
        "Fill cells of an existing .xlsx (template) keeping its formatting and other sheets. The first call "
        "makes a copy under exports/; pass the returned path to keep filling. Rows by number or by key_column. "
        "Returns download_url for the user."
    ),
)
def api_fill_table(body: FillTableBody, request: Request):
    return _http_errors(
        table_tool.fill_table,
        body.path,
        [u.model_dump() for u in body.updates],
        body.sheet,
        body.header_row,
        body.key_column,
        body.allowed_values,
        None,
        _public_base(request),
    )


@app.post(
    "/start_table_job",
    summary="Process a big spreadsheet row by row in the background",
    description=(
        "For tables too big for the chat (hundreds to tens of thousands of rows): the server walks the rows in "
        "batches, asks the model to fill the output columns for each batch (optionally looking every row up "
        "in a reference document such as a catalog) and writes results into a copy of the file. Returns job_id "
        "at once; poll table_job_status. Try max_rows=30 first and show the user the sample."
    ),
)
def api_start_table_job(body: StartTableJobBody, request: Request):
    return _http_errors(
        table_jobs.start_job,
        body.path,
        body.instruction,
        body.input_columns,
        {k: v.model_dump() for k, v in body.outputs.items()},
        sheet=body.sheet,
        header_row=body.header_row,
        data_start_row=body.data_start_row,
        filters=[f.model_dump() for f in body.filters],
        document=body.document,
        lookup_column=body.lookup_column,
        batch_size=body.batch_size,
        workers=body.workers,
        max_rows=body.max_rows,
        example_rows=body.example_rows,
        skip_filled=body.skip_filled,
        public_base_url=_public_base(request),
    )


@app.get(
    "/table_job_status",
    summary="Progress and result of a table job",
    description="Rows done/filled, errors, unresolved and low-confidence rows, download_url when finished.",
)
def api_table_job_status(job_id: str = Query(..., description="job_id from start_table_job")):
    return _http_errors(table_jobs.job_status, job_id)


@app.post(
    "/fetch_url",
    summary="Browse an external web page",
    description=(
        "Fetch a public URL and extract the main readable content "
        "(similar to ChatGPT / DeepSeek web browsing). "
        "Complement chat attachments when the user provides a link: "
        "pass the exact URL from the message, do not invent paths."
    ),
)
def api_fetch_url(body: FetchUrlBody):
    try:
        return fetch_page(
            body.url,
            max_chars=body.max_chars,
            timeout_sec=config.FETCH_TIMEOUT_SEC,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {e}") from e


@app.post(
    "/write_excel",
    summary="Export tables to Excel for the user",
    description=(
        "Create an .xlsx from structured tables: extract rows from the "
        "source text, pass headers and rows for one or more sheets. "
        "Returns download_url — put it in the reply as a markdown link so the user "
        "can download the file in the browser. Do NOT invent table data."
    ),
)
def api_write_excel(body: WriteExcelBody, request: Request):
    try:
        return write_excel(
            filename=body.filename,
            sheets=[s.model_dump() for s in body.sheets],
            public_base_url=_public_base(request),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Excel write failed: {e}") from e


@app.get(
    "/download_file",
    summary="Download an exported spreadsheet",
    description="Browser download for files under exports/. Not for the model to call.",
    include_in_schema=False,
)
def api_download_file(
    path: str = Query(
        ...,
        description="Relative path under data root, e.g. exports/2026-03-26/x.xlsx",
    ),
):
    try:
        file_path = resolve_export_file(path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content_disposition_type="attachment",
    )
