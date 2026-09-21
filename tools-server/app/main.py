"""OpenAPI Tool Server for Open WebUI: documents, fetch URL, browser agent."""

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

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import config
from .document_tool import list_directory, read_document, read_folder_documents
from .web_tool import fetch_page

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
)
logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)
logging.getLogger("langchain").setLevel(logging.INFO)

app = FastAPI(
    title="Tender Tools API",
    version="1.1.0",
    description=(
        "Tools for the tender agent: server documents, simple URL fetch, "
        "and a browser agent (DOM + screenshot tools, LLM chooses the path)."
    ),
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
            "Relative path under data root, e.g. 'tenders/spec.pdf' "
            "or 'catalogs/prices.xlsx'."
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
        description="Public http(s) URL of a tender page or any web page to read.",
    )
    max_chars: int = Field(
        default=config.DEFAULT_MAX_CHARS,
        description="Max characters of extracted page text.",
    )


class PlatformTaskBody(BaseModel):
    platform_url: str = Field(
        ...,
        description="Tender platform base URL, e.g. https://zakupki.gov.ru/",
    )
    keywords: str = Field(
        ...,
        description="Search keywords for new tenders on the platform.",
    )
    max_new_tenders: int | None = Field(
        None,
        description=f"Max NEW tenders to process (default {config.PLATFORM_MAX_NEW_TENDERS}).",
    )
    max_steps: int | None = Field(
        None,
        description=f"Max agent steps (default {config.PLATFORM_MAX_STEPS}).",
    )
    download_subdir: str | None = Field(
        None,
        description=(
            "Optional override of session folder under data/tenders/. "
            "Default: platform host, e.g. zakupki_gov_ru."
        ),
    )
    instruction: str | None = Field(
        None,
        description=(
            "Optional override of the agent user request "
            "(used by debug/integration scripts)."
        ),
    )


class BrowserTaskBody(BaseModel):
    task: str = Field(
        ...,
        description=(
            "Natural-language task for the browser agent, e.g. "
            "'Скачай все документы со страницы тендера и кратко опиши лот'."
        ),
    )
    url: str | None = Field(
        None,
        description="Optional starting URL (agent will navigate here first).",
    )
    max_steps: int | None = Field(
        None,
        description=f"Max tool steps (default {config.BROWSER_MAX_STEPS}).",
    )
    download_subdir: str | None = Field(
        None,
        description=(
            "Optional folder name under data/tenders/ for downloads "
            "(default data/tenders/_browser)."
        ),
    )


@app.get("/health", summary="Health check")
def health():
    return {
        "status": "ok",
        "data_root": str(config.DATA_ROOT),
        "browser_agent": {
            "llm_configured": bool(
                config.AGENT_LLM_BASE_URL and config.AGENT_LLM_API_KEY
            ),
            "model": config.AGENT_LLM_MODEL,
            "vl_model": config.AGENT_VL_MODEL,
            "vl_enabled": config.AGENT_VL_ENABLED,
            "headless": config.BROWSER_HEADLESS,
        },
        "platform_agent": {
            "llm_configured": bool(
                config.AGENT_LLM_BASE_URL and config.AGENT_LLM_API_KEY
            ),
            "model": config.AGENT_LLM_MODEL,
            "vl_model": config.AGENT_VL_MODEL,
            "max_steps": config.PLATFORM_MAX_STEPS,
            "max_new_tenders": config.PLATFORM_MAX_NEW_TENDERS,
            "seen_tenders_db": str(config.SEEN_TENDERS_DB),
        },
    }


@app.get(
    "/list_documents",
    summary="List documents on the server",
    description=(
        "List files and folders under the server data directory "
        "(tenders/, catalogs/, uploads/). Use this before reading."
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
        "Extract text from a file on the server the same way Open WebUI does "
        "for chat attachments (PDF/DOCX/TXT/CSV/XLSX/ZIP/...). "
        "Returns full text for the model context (with optional truncation)."
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
        "Extract text from every supported file in a folder "
        "(like attaching multiple chat documents at once)."
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
        "Use for tender portal pages or any external link."
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
    "/run_platform_task",
    summary="Monitor a tender platform by keywords (LangChain agent)",
    description=(
        "LangChain agent: navigate platform, search by keywords, deduplicate via SQLite, "
        "open new tender cards and download documentation. Uses one multimodal model "
        "(AGENT_LLM_MODEL) for tool-calling; screenshots are attached inline (no separate VL call)."
    ),
)
async def api_run_platform_task(body: PlatformTaskBody):
    try:
        from .browser_agent.agent import run_platform_task

        return await run_platform_task(
            platform_url=body.platform_url,
            keywords=body.keywords,
            max_new_tenders=body.max_new_tenders,
            max_steps=body.max_steps,
            download_subdir=body.download_subdir,
            instruction=body.instruction,
        )
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Platform agent dependencies missing: {e}",
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Platform agent failed: {e}") from e


@app.post(
    "/run_browser_task",
    summary="Run browser agent on a tender URL / task",
    description=(
        "Starts an internal browser agent with tools: navigate, screenshot, "
        "click_xy, type_text, list_download_links, download_url, get_page_text, finish. "
        "The agent itself chooses DOM vs vision path — no fixed grounding stage. "
        "Requires AGENT_LLM_* env (prefer a VL-capable OpenAI-compatible model)."
    ),
)
async def api_run_browser_task(body: BrowserTaskBody):
    try:
        from .browser_tool.agent import run_browser_task

        return await run_browser_task(
            task=body.task,
            url=body.url,
            max_steps=body.max_steps,
            download_subdir=body.download_subdir,
        )
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Playwright is not installed in the container: {e}",
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Browser agent failed: {e}") from e
