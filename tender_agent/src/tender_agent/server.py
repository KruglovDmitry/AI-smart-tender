"""OpenAPI-сервер браузерного агента для Open WebUI."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .api.service import run_task
from .config import Settings

app = FastAPI(
    title="Tender Agent",
    version="0.1.0",
    description=(
        "Browser agent that opens a procurement site and returns tenders. "
        "Separate from the document tools server."
    ),
)


class SearchTendersBody(BaseModel):
    task: str = Field(
        ...,
        description=(
            "Full user request. It must include the site URL and a keyword or topic. "
            "May include how many latest tenders to return and whether to download files. "
            "Pass the user message unchanged."
        ),
    )


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _settings() -> Settings:
    data_dir = Path(os.getenv("TENDER_AGENT_DATA_DIR", "/data/tenders")).expanduser()
    base = os.getenv("TENDER_AGENT_LLM_BASE_URL") or os.getenv("AGENT_LLM_BASE_URL", "")
    key = os.getenv("TENDER_AGENT_LLM_API_KEY") or os.getenv("AGENT_LLM_API_KEY", "")
    model = os.getenv("TENDER_AGENT_LLM_MODEL") or os.getenv("AGENT_LLM_MODEL", "deepseek-flash")
    return Settings(
        data_dir=data_dir,
        llm_base_url=base.rstrip("/"),
        llm_api_key=key,
        llm_model=model,
        headless=_flag("TENDER_AGENT_HEADLESS", True),
        default_limit=int(os.getenv("TENDER_AGENT_DEFAULT_LIMIT", "5")),
        max_actions=int(os.getenv("TENDER_AGENT_MAX_ACTIONS", "40")),
        max_llm_calls=int(os.getenv("TENDER_AGENT_MAX_LLM_CALLS", "12")),
        max_retries=int(os.getenv("TENDER_AGENT_MAX_RETRIES", "2")),
        timeout_seconds=int(os.getenv("TENDER_AGENT_TIMEOUT_SECONDS", "120")),
        max_pages=int(os.getenv("TENDER_AGENT_MAX_PAGES", "5")),
        max_downloads=int(os.getenv("TENDER_AGENT_MAX_DOWNLOADS", "20")),
        max_download_bytes=int(os.getenv("TENDER_AGENT_MAX_DOWNLOAD_BYTES", "20000000")),
    )


@app.get("/health", summary="Health check")
def health():
    settings = _settings()
    ready = bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model)
    return {
        "status": "ok" if ready else "degraded",
        "data_dir": str(settings.data_dir),
        "llm_configured": ready,
        "model": settings.llm_model,
    }


@app.post(
    "/search_tenders",
    summary="Search tenders with the browser agent",
    operation_id="search_tenders",
    description=(
        "Open a procurement website and return tenders. "
        "Use when the user asks to find purchases on a site by a keyword or topic, "
        "return the latest N by publication date, or download card documents. "
        "Pass the user request unchanged in task. The task must contain the site URL "
        "and a keyword. Do not use this tool to submit bids or analyze document text."
    ),
)
async def search_tenders(body: SearchTendersBody):
    text = (body.task or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустое задание. Нужны URL площадки и ключевое слово.")
    try:
        result = await run_task(text, settings=_settings())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Tender search failed: {exc}") from exc
    return result.model_dump(mode="json")
