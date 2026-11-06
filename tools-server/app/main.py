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

from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config
from . import document_index
from .document_tool.spreadsheets import operations as table_operations
from .document_tool.spreadsheets import jobs as table_jobs
from .document_tool import list_directory, read_document, read_folder_documents
from .document_tool.artifacts import resolve_export_file
from .document_tool.spreadsheets.create import write_excel
from .observe import begin_call, call_fields, format_fields, get_logger, hint, record, setup_logging, skip_path, summarize
from .web_tool import fetch_page

setup_logging()


@asynccontextmanager
async def _lifespan(_app):
    # uvicorn заново настраивает журналы после импорта приложения.
    setup_logging()
    yield


app = FastAPI(
    title="Tender Tools API",
    version="1.5.0",
    description="Server documents, URL fetch, and Excel export.",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _log_request(request: Request, call_next):
    import time

    if skip_path(request.url.path):
        return await call_next(request)
    begin_call()
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        get_logger().exception("request failed %s %s", request.method, request.url.path)
        raise
    elapsed = int((time.perf_counter() - started) * 1000)
    status = getattr(response, "status_code", 0)
    level = logging.INFO if status < 400 else logging.WARNING if status < 500 else logging.ERROR
    detail = format_fields(call_fields())
    get_logger("tools.http").log(level, "%s %s %s %dms %s", request.method, request.url.path, status, elapsed, detail)
    return response


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
    max_total_chars: int = Field(
        150_000,
        description="Budget for full text of the folder; files beyond it come as outlines to read with search and read_pdf or read_file.",
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


class ReadPdfBody(BaseModel):
    path: str = Field(..., description="PDF available under server DATA_ROOT; not a chat attachment.")
    mode: Literal["auto", "full", "index"] = "auto"
    pages: str | None = Field(None, description="Optional selected pages, e.g. 3,8-10. Otherwise use start_page.")
    start_page: int = Field(1, ge=1)
    text_offset: int = Field(0, ge=0, description="Resume offset from next_cursor; keep all other options unchanged.")
    max_pages: int = Field(8, ge=1, le=10)
    max_chars: int = Field(40000, ge=1, le=120000)
    include_tables: bool = True
    include_layout: bool = True
    visual_mode: Literal["off", "auto", "required"] = "auto"
    max_visual_pages: int = Field(2, ge=0, le=10, description="Maximum vision attempts per call; 0 disables attempts.")


class ReadDocxBody(BaseModel):
    path: str = Field(..., description="DOCX under server DATA_ROOT; not a chat attachment.")
    mode: Literal["auto", "full", "index"] = "auto"
    parts: str | None = Field(None, description="Logical parts, e.g. 2,5-7; not physical pages.")
    start_part: int = Field(1, ge=1)
    text_offset: int = Field(0, ge=0)
    max_parts: int = Field(8, ge=1, le=10)
    max_chars: int = Field(40000, ge=1, le=120000)
    visual_mode: Literal["off", "auto", "required"] = "auto"
    max_visual_parts: int = Field(2, ge=0, le=10)



class ReadFileBody(ReadDocxBody):
    path: str = Field(..., description='File under server DATA_ROOT, not a chat attachment.')
    parts: str | None = Field(None, description='Selected logical parts, slides or frames: 2,5-7. Check page_unit in response.')
    visual_mode: Literal['off', 'auto', 'required'] = 'off'


class ViewImageBody(BaseModel):
    path: str = Field(..., description='Raster image under server DATA_ROOT.')
    frame: int = Field(1, ge=1, description='1-based TIFF/GIF/image frame.')
    question: str = ''
    detail: Literal['auto', 'low', 'high'] = 'auto'
    bbox: list[int] | None = Field(None, min_length=4, max_length=4, description='Crop [left,top,right,bottom], in pixels after EXIF orientation.')


class ViewSlideBody(BaseModel):
    path: str
    slide: int = Field(1, ge=1)
    question: str = ''
    detail: Literal['auto', 'low', 'high'] = 'auto'
    bbox: list[float] | None = Field(None, min_length=4, max_length=4, description='Crop in rendered PDF points; not EMU.')


class ViewPresentationImageBody(BaseModel):
    path: str
    slide: int = Field(1, ge=1)
    image_id: str = Field(..., description='Image ID from read_presentation layout.')
    question: str = ''
    detail: Literal['auto', 'low', 'high'] = 'auto'


class OpenArchiveMemberBody(BaseModel):
    path: str
    member: str = Field(..., description='Exact unique member name from list_archive. Does not execute files.')


class DocxBlock(BaseModel):
    kind: Literal["paragraph", "heading", "bullet", "numbered", "table", "image", "page_break"] = "paragraph"
    text: str = ""
    level: int = Field(1, ge=1, le=9)
    bold: bool = False
    italic: bool = False
    rows: list[list[str | int | float | None]] = Field(default_factory=list)
    header: bool = True
    image_path: str | None = None
    width_inches: float = Field(5, ge=0.1, le=6.5)


class WriteDocxBody(BaseModel):
    filename: str = "document.docx"
    title: str = ""
    blocks: list[DocxBlock] = Field(..., min_length=1, max_length=10000)


class ViewDocxImageBody(BaseModel):
    path: str
    image_id: str = Field(..., description="Image ID returned by read_docx/document_info.")
    question: str = ""
    detail: Literal["auto", "low", "high"] = "auto"


class ViewPageBody(BaseModel):
    path: str = Field(..., description="Relative path under data root to a PDF.")
    bbox: list[float] | None = Field(None, min_length=4, max_length=4, description="Optional [x0,y0,x1,y1] in PDF points from read_pdf layout, inside page bounds.")
    detail: Literal["auto", "low", "high"] = "auto"
    page: int = Field(..., description="Page number.")
    question: str = Field(
        "",
        description="What to look at, e.g. 'какие клеммы на схеме подключения' (empty = describe the page).",
    )


class DescribeTableBody(BaseModel):
    path: str = Field(..., description="Relative path under data root to .xlsx/.xlsm/.xls/.csv/.tsv.")
    sheet: str | None = Field(None, description="Sheet name; empty = all sheets.")
    header_row: int | None = Field(None, description="Header row number if auto-detection is wrong.")


class TableFilter(BaseModel):
    column: str = Field(..., description="Column letter (B), number or header text.")
    op: str = Field("eq", description="eq | ne | contains | not_contains | startswith | in | empty | not_empty | gt | lt")
    value: Any = Field(None, description="Value to compare; for 'in' a list or 'a|b|c'.")


class QueryTableBody(BaseModel):
    read_mode: Literal["values", "formulas", "both"] = "values"
    path: str = Field(..., description="Relative path under data root to .xlsx/.xlsm/.xls/.csv/.tsv.")
    sheet: str | None = Field(None, description="Sheet name; empty = first sheet.")
    header_row: int | None = Field(None, description="Header row number; empty = auto.")
    data_start_row: int | None = Field(None, description="First data row when service rows follow the header.")
    columns: list[str] | None = Field(None, description="Columns to return (letters or headers); empty = all.")
    filters: list[TableFilter] = Field(default_factory=list, description="All filters must match.")
    offset: int = Field(0, description="Skip this many matched rows (paging).")
    limit: int = Field(50, description="Rows to return, max 200.")
    distinct: str | None = Field(None, description="Instead of rows, count distinct values of this column.")


class TableUpdate(BaseModel):
    mark: str | None = Field(None, description="Optional review note for edited cells.")
    row: int | None = Field(None, description="Sheet row number to write.")
    key: str | None = Field(None, description="Value of key_column identifying the row(s), instead of row.")
    values: dict[str, Any] = Field(..., description="{column letter or header: value}")


class FillTableBody(BaseModel):
    overwrite_existing: bool = Field(False, description="Explicitly allow editing/clearing populated cells; empty cells only by default.")
    replace_formulas: bool = Field(False, description="Explicitly allow replacing formulas, also requires overwrite_existing=true.")
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
    reference: str | None = Field(None, description="Separate classification source: okpd2. Auto-detected for columns named Код ОКПД2/OKPD2; other outputs use the catalog.")
    description: str = Field("", description="What to put into this column and how to decide it.")
    allowed: list[str] | None = Field(None, description="Allowed values (e.g. from the template's reference sheet).")


class StartTableJobBody(BaseModel):
    quality_mode: str = Field("balanced", description="balanced: one research round, review uncertain rows and retain disputed sourced values highlighted for manual checking; strict: up to four rounds and reject unapproved values. Quote and dictionary checks always apply.")
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


def _ok(result):
    record(**summarize(result))
    return result


def _fail(status: int, detail: str, exc: BaseException):
    record(error=detail[:300])
    if status >= 500:
        get_logger().exception("tool failed")
    raise HTTPException(status_code=status, detail=detail) from exc


def _http_errors(fn, *args, **kwargs):
    record(tool=getattr(fn, "__name__", ""), **hint(args, kwargs))
    try:
        return _ok(fn(*args, **kwargs))
    except FileNotFoundError as e:
        _fail(404, str(e), e)
    except (ValueError, KeyError, IsADirectoryError) as e:
        _fail(400, str(e), e)
    except PermissionError as e:
        _fail(403, str(e), e)
    except Exception as e:
        _fail(500, f"{type(e).__name__}: {e}", e)


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
    record(tool="list_documents", path=path)
    try:
        return _ok(list_directory(path, recursive=recursive))
    except FileNotFoundError as e:
        _fail(404, str(e), e)
    except NotADirectoryError as e:
        _fail(400, str(e), e)
    except PermissionError as e:
        _fail(403, str(e), e)


@app.post(
    "/read_document",
    summary="Read one document from the server",
    description=(
        "Extract text from a file on the server data volume "
        "(PDF/DOCX/TXT/CSV/XLSX/ZIP/...). "
        "Do NOT call this for files attached in the chat — use chat context instead. "
        "Use only for paths under tenders/, catalogs/, uploads/ on the server. "
        "For big PDFs use document_info/search_document/read_pdf; for spreadsheets describe_table/query_table."
    ),
)
def api_read_document(body: ReadDocumentBody):
    record(tool="read_document", path=body.path)
    try:
        return _ok(read_document(body.path, max_chars=body.max_chars))
    except FileNotFoundError as e:
        _fail(404, str(e), e)
    except (IsADirectoryError, ValueError) as e:
        _fail(400, str(e), e)
    except PermissionError as e:
        _fail(403, str(e), e)
    except Exception as e:
        _fail(500, f"Extract failed: {e}", e)


@app.post(
    "/read_folder",
    summary="Read all documents from a server folder",
    description=(
        "Start here for a tender folder (tenders/, catalogs/, ...): small files come in full, the rest as "
        "outlines (pages and titles, sheets and columns) to read further with search_document / read_pdf / "
        "query_table. Not a substitute for chat attachments."
    ),
)
def api_read_folder(body: ReadFolderBody):
    record(tool="read_folder", path=body.path)
    try:
        return _ok(read_folder_documents(
            body.path,
            max_files=body.max_files,
            max_chars_per_file=body.max_chars_per_file,
            max_total_chars=body.max_total_chars,
            recursive=body.recursive,
        ))
    except FileNotFoundError as e:
        _fail(404, str(e), e)
    except NotADirectoryError as e:
        _fail(400, str(e), e)
    except PermissionError as e:
        _fail(403, str(e), e)


@app.post(
    "/document_info",
    summary="Outline of a large document",
    description=(
        "Page count and a one-line title per page for a PDF/DOCX on the server, plus pages that needed OCR. "
        "Use for big files (catalogs, tender documentation) instead of read_document; then search_document "
        "and read_pdf with the page numbers you need."
    ),
)
def api_document_info(body: DocumentInfoBody):
    return _http_errors(document_index.document_info, body.path, body.page_from, body.page_to)


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
    return _http_errors(document_index.search_document, body.path, body.query, body.max_results, body.mode)


@app.post(
    "/read_pages",
    summary="Read selected pages of a document",
    description="Text and tables of chosen pages. Prefer read_pdf: it places the grid after the surrounding prose and can call vision.",
)
def api_read_pages(body: ReadPagesBody):
    return _http_errors(document_index.read_pages, body.path, body.pages, body.include_tables, body.max_chars)


@app.post(
    "/read_pdf",
    summary="Read a small PDF or navigate a large PDF with layout and optional vision",
    description=(
        "Use for server PDFs. Auto reads small documents or returns a bounded index for large ones. "
        "For exhaustive reading use mode=full and follow next_cursor with unchanged options. "
        "Returns page coverage, text/table content, layout coordinates and warnings. "
        "visual_mode=auto calls the configured Qwen-VL-compatible endpoint on visual/scanned pages, within max_visual_pages. "
        "Off disables vision. Use view_page for a specific page or layout bbox."
    ),
)
def api_read_pdf(body: ReadPdfBody):
    from .document_index.reading import read_pdf

    return _http_errors(read_pdf, **body.model_dump())


@app.post("/read_docx", summary="Read DOCX structure, logical parts and embedded images",
    description="Auto reads small DOCX or returns an index for large documents. Use mode=full and next_cursor for exhaustive reading. Logical parts are not physical pages; use view_docx_page for rendered pages.")
def api_read_docx(body: ReadDocxBody):
    from .document_index.reading import read_docx
    return _http_errors(read_docx, **body.model_dump())


@app.post("/write_docx", summary="Create and save a new Word document",
    description="Create headings, paragraphs, lists, tables, images and page breaks. Saves a verified DOCX copy under exports and returns a download link. Does not modify an existing document.")
def api_write_docx(body: WriteDocxBody, request: Request):
    from .document_tool.docx.create import create_docx
    return _http_errors(create_docx, body.filename, [b.model_dump() for b in body.blocks], body.title, _public_base(request))


@app.get("/list_docx_images", summary="List embedded DOCX images with paging; no vision calls")
def api_list_docx_images(path: str, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=50)):
    from .document_tool.docx.vision import list_images
    return _http_errors(list_images, path, offset, limit)


@app.post("/view_docx_image", summary="Inspect a DOCX embedded image with the vision model")
def api_view_docx_image(body: ViewDocxImageBody):
    from .document_tool.docx.vision import view_image
    return _http_errors(view_image, body.path, body.image_id, body.question, body.detail)


@app.post("/render_docx", summary="Render DOCX to PDF and discover physical page count; requires LibreOffice")
def api_render_docx(body: DocumentInfoBody):
    from .document_tool.docx.render import render_document
    return _http_errors(render_document, body.path)


@app.post("/view_docx_page", summary="Inspect a rendered physical DOCX page",
    description="Requires LibreOffice. Renders a copy to PDF and uses the common PDF viewer. Physical page number differs from logical part number.")
def api_view_docx_page(body: ViewPageBody):
    from .document_tool.docx.vision import view_page
    return _http_errors(view_page, body.path, body.page, body.question, body.bbox, body.detail)



@app.post('/read_file', summary='Read supported files with one bounded index and continuation',
    description='Auto returns small content or a large-file index. For exhaustive reading use mode=full and next_cursor. Check page_unit: PDF pages, DOCX/text parts, slides, image frames or archive manifest parts. Vision is off by default. Use table tools for spreadsheets.')
def api_read_file(body: ReadFileBody):
    from .document_index.reading import read_file
    return _http_errors(read_file, **body.model_dump())


@app.post('/read_text', summary='Read text, source code, JSON, XML and HTML without executing it',
    description='Preserves decoded whitespace and source text, with character offsets and line ranges. Large files use the shared index and continuation. HTML/XML remain source markup; scripts, imports, links and includes are not executed.')
def api_read_text(body: ReadFileBody):
    from .document_index.reading import read_file
    return _http_errors(read_file, **body.model_dump(), expected_format='text')


@app.post('/read_presentation', summary='Read presentation slides, tables, notes and image references',
    description='PPTX is native. Legacy PPT requires LibreOffice. parts/start_part address slides, page_unit=slide. Shape order is XML order; group coordinates are local EMU. Vision is optional; use view_slide for full layout or view_presentation_image for one embedded picture.')
def api_read_presentation(body: ReadFileBody):
    from .document_index.reading import read_file
    return _http_errors(read_file, **body.model_dump(), expected_format='presentation')


@app.post('/read_image', summary='Read raster frame metadata with optional visual interpretation',
    description='PNG/JPEG/TIFF/BMP/WebP/GIF. Parts are frames. Vision is off by default; set visual_mode=auto/required or call view_image. Metadata alone is not transcribed image content. No implicit OCR or model calls during uploads.')
def api_read_image(body: ReadFileBody):
    from .document_index.reading import read_file
    return _http_errors(read_file, **body.model_dump(), expected_format='image')


@app.post('/view_image', summary='Inspect a raster frame or crop with the configured Qwen-VL model')
def api_view_image(body: ViewImageBody):
    from .document_tool.images.vision import view_frame
    return _http_errors(view_frame, **body.model_dump())


@app.post('/ocr_image', summary='Run local OCR on one raster frame; no cloud vision call')
def api_ocr_image(body: ViewImageBody):
    from .document_tool.images.vision import ocr_frame
    if body.bbox is not None:
        raise HTTPException(status_code=400, detail='ocr_image processes a full frame. Use view_image for crops.')
    return _http_errors(ocr_frame, body.path, body.frame)


@app.post('/view_slide', summary='Inspect a full rendered presentation slide; requires LibreOffice')
def api_view_slide(body: ViewSlideBody):
    from .document_tool.presentations.vision import view_slide
    return _http_errors(view_slide, **body.model_dump())


@app.post('/view_presentation_image', summary='Inspect an embedded slide picture without a renderer')
def api_view_presentation_image(body: ViewPresentationImageBody):
    from .document_tool.presentations.vision import view_image
    return _http_errors(view_image, **body.model_dump())


@app.get('/list_archive', summary='List ZIP/RAR/7z members without reading all documents',
    description='Paginated manifest. Flags encrypted, oversized, unsafe and linked entries. RAR extraction needs an unrar/bsdtar backend. Nested archives are not expanded automatically.')
def api_list_archive(path: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    from .document_tool.archives.read import list_archive
    return _http_errors(list_archive, path, offset, limit)


@app.post('/open_archive_member', summary='Open one archive member as a separate server file',
    description='Returns a safe content-addressed path under DATA_ROOT for format tools. Does not execute files or change the source archive. Rejects links, traversal paths, encrypted entries and excessive sizes.')
def api_open_archive_member(body: OpenArchiveMemberBody):
    from .document_tool.archives.read import open_member
    return _http_errors(open_member, **body.model_dump())


@app.get(
    "/document_skill",
    summary="Load instructions for a document format",
    description=(
        "Call once for file_type=pdf, spreadsheets, docx, presentations, images, text, archives or legacy to load format instructions. "
        "Chat attachments stay with Open WebUI's existing processing; do not re-read them automatically."
    ),
)
def api_document_skill(file_type: Literal["pdf", "spreadsheets", "docx", "presentations", "images", "text", "archives", "legacy"] = Query("pdf")):
    from .document_tool.skills import document_skill

    return _http_errors(document_skill, file_type)


@app.put("/process", include_in_schema=False)
async def api_owui_process(request: Request):
    """Внешний загрузчик документов для «Знаний» Open WebUI. Не инструмент модели, поэтому скрыт из схемы."""
    from starlette.concurrency import run_in_threadpool

    from .integrations.open_webui.loader import process_upload

    data = await request.body()
    if not data:
        record(tool="process_upload", error="Empty body")
        raise HTTPException(status_code=400, detail="Empty body")
    record(tool="process_upload", filename=request.headers.get("X-Filename", "")[:180])
    return await run_in_threadpool(
        _http_errors, process_upload, data, request.headers.get("X-Filename", ""), request.headers.get("Content-Type")
    )


@app.post(
    "/view_page",
    summary="Look at a document page as an image",
    description=(
        "Render a PDF page and ask a vision model a question about it: diagrams, drawings, photos, "
        "tables printed as images. Pass the page number and a specific question. Answers are cached."
    ),
)
def api_view_page(body: ViewPageBody):
    from .document_tool.pdf import vision

    return _http_errors(vision.view_page, body.path, body.page, body.question, body.bbox, body.detail)


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
    return _http_errors(table_operations.describe_table, body.path, body.sheet, body.header_row)


@app.post(
    "/query_table",
    summary="Get spreadsheet rows by filter",
    description="Rows matching filters with paging (max 200 per call), formula metadata and read_mode=values|formulas|both, or distinct counts.",
)
def api_query_table(body: QueryTableBody):
    return _http_errors(
        table_operations.query_table,
        body.path,
        body.sheet,
        body.header_row,
        body.data_start_row,
        body.columns,
        [f.model_dump() for f in body.filters],
        body.offset,
        body.limit,
        body.distinct,
        body.read_mode,
    )


@app.post(
    "/fill_table",
    summary="Write values into a copy of a spreadsheet",
    description=(
        "Fill cells of an existing .xlsx (template) keeping its formatting and other sheets. The first call "
        "makes a copy under exports/; pass the returned path to keep filling. Rows by number or by key_column. "
        "Existing values and formulas are protected by default; use explicit editing flags to replace them. "
        "Saved XLSX is reopened and verified before publication. Returns download_url for the user."
    ),
)
def api_fill_table(body: FillTableBody, request: Request):
    return _http_errors(
        table_operations.fill_table,
        body.path,
        [u.model_dump() for u in body.updates],
        body.sheet,
        body.header_row,
        body.key_column,
        body.allowed_values,
        None,
        _public_base(request),
        body.overwrite_existing,
        body.replace_formulas,
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
        quality_mode=body.quality_mode,
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
    record(tool="fetch_url", path=body.url[:180])
    try:
        return _ok(fetch_page(
            body.url,
            max_chars=body.max_chars,
            timeout_sec=config.FETCH_TIMEOUT_SEC,
        ))
    except ValueError as e:
        _fail(400, str(e), e)
    except Exception as e:
        _fail(502, f"Fetch failed: {e}", e)


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
    record(tool="write_excel", path=body.filename)
    try:
        return _ok(write_excel(
            filename=body.filename,
            sheets=[s.model_dump() for s in body.sheets],
            public_base_url=_public_base(request),
        ))
    except ValueError as e:
        _fail(400, str(e), e)
    except PermissionError as e:
        _fail(403, str(e), e)
    except Exception as e:
        _fail(500, f"Excel write failed: {e}", e)


@app.get(
    "/download_file",
    summary="Download an exported document",
    description="Browser download for files under exports/. Not for the model to call.",
    include_in_schema=False,
)
def api_download_file(
    path: str = Query(
        ...,
        description="Relative path under data root, e.g. exports/2026-03-26/x.xlsx",
    ),
):
    record(tool="download_file", path=path)
    try:
        file_path = resolve_export_file(path)
    except FileNotFoundError as e:
        _fail(404, str(e), e)
    except PermissionError as e:
        _fail(403, str(e), e)
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type={".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ".pdf":"application/pdf", ".csv":"text/csv", ".xls":"application/vnd.ms-excel"}.get(
                        file_path.suffix.lower(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        content_disposition_type="attachment",
    )


def _inline_refs(node: Any, schemas: dict[str, Any], depth: int = 0) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/") and depth < 8:
            target = schemas.get(ref.rsplit("/", 1)[-1], {})
            extra = {k: v for k, v in node.items() if k != "$ref"}
            return _inline_refs({**target, **extra}, schemas, depth + 1)
        return {k: _inline_refs(v, schemas, depth) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(v, schemas, depth) for v in node]
    return node


def _openapi_without_refs() -> dict[str, Any]:
    """Open WebUI не разрешает $ref внутри вложенных типов (например, словарь {колонка: JobOutput}) и отказывается
    звать такой инструмент. Отдаём схему, где все ссылки на компоненты подставлены на место."""
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    components = schema.get("components", {}).get("schemas", {})
    schema["paths"] = _inline_refs(schema.get("paths", {}), components)
    app.openapi_schema = schema
    return schema


app.openapi = _openapi_without_refs
