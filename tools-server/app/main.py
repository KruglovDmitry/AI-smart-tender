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
    version="1.4.0",
    description="Server documents, URL fetch, and Excel export.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def _public_base(request: Request) -> str:
    configured = config.TOOLS_PUBLIC_BASE_URL
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


@app.get("/health", summary="Health check")
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
        "Use only for paths under tenders/, catalogs/, uploads/ on the server."
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
