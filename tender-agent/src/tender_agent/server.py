"""OpenAPI-сервер браузерного агента для Open WebUI."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent.vision import vision_available
from .agent_settings import apply_overrides, describe, effective, save_overrides, settings_path
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
    download: bool | None = Field(
        None,
        description=(
            "Download tender documents: true or false. Leave empty to follow the task text "
            "(«скачай» / «не скачивай»)."
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
        timeout_seconds=int(os.getenv("TENDER_AGENT_TIMEOUT_SECONDS", "300")),
        max_pages=int(os.getenv("TENDER_AGENT_MAX_PAGES", "5")),
        card_timeout_seconds=int(os.getenv("TENDER_AGENT_CARD_TIMEOUT_SECONDS", "90")),
        card_max_steps=int(os.getenv("TENDER_AGENT_CARD_MAX_STEPS", "10")),
        downloads_enabled=_flag("TENDER_AGENT_DOWNLOADS", True),
        max_downloads=int(os.getenv("TENDER_AGENT_MAX_DOWNLOADS", "30")),
        max_download_bytes=int(os.getenv("TENDER_AGENT_MAX_DOWNLOAD_BYTES", "20000000")),
        vision_enabled=_flag("TENDER_AGENT_VISION_ENABLED", False),
        vision_max_calls=int(os.getenv("TENDER_AGENT_VISION_MAX_CALLS", "2")),
        vision_max_steps=int(os.getenv("TENDER_AGENT_VISION_MAX_STEPS", "10")),
        trace_enabled=_flag("TENDER_AGENT_TRACE", True),
        trace_screenshots=os.getenv("TENDER_AGENT_TRACE_SCREENSHOTS", "errors"),
        store_enabled=_flag("TENDER_AGENT_STORE", True),
        db_url=os.getenv("TENDER_AGENT_DB_URL", ""),
    )


@app.on_event("startup")
def create_schema() -> None:
    # Таблицы создаются при старте, а не при первом прогоне: дашборд читает базу сразу
    # и на пустой новой базе показывал ошибку «База закупок недоступна».
    settings = _settings()
    if not settings.store_enabled:
        return
    from .storage.tenders import TenderStore

    try:
        TenderStore(settings.db_url or settings.data_dir / "_agent" / "agent.sqlite3")
    except Exception as exc:  # база недоступна — агент всё равно работает, ошибку покажет дашборд
        print(f"Tender DB not ready: {exc}")


@app.get("/health", summary="Health check")
def health():
    settings = apply_overrides(_settings())
    ready = bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model)
    return {
        "status": "ok" if ready else "degraded",
        "data_dir": str(settings.data_dir),
        "llm_configured": ready,
        "model": settings.llm_model,
        "vision_enabled": settings.vision_enabled,
        "vision_available": vision_available(),
        "headless": settings.headless,
    }


# ---------- настройки агента для дашборда (вкладка «Агент») ----------
# Не попадают в OpenAPI: иначе Open WebUI предложил бы их модели в чате как инструменты.


class SettingsBody(BaseModel):
    values: dict = Field(default_factory=dict, description="key → значение; null или пустая строка — сбросить")


class CheckBody(BaseModel):
    target: str = Field(..., description="dom | planner | tars")


@app.get("/settings", include_in_schema=False)
def get_settings():
    data_dir = _settings().data_dir
    return {"options": describe(data_dir), "vision_available": vision_available(),
            "path": str(settings_path(data_dir))}


@app.put("/settings", include_in_schema=False)
def put_settings(body: SettingsBody):
    data_dir = _settings().data_dir
    try:
        save_overrides(data_dir, body.values)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_settings()


@app.post("/settings/check", include_in_schema=False)
async def check_settings(body: CheckBody):
    """Короткий запрос к модели, чтобы проверить адрес, ключ и имя модели до запуска поиска."""
    data_dir = _settings().data_dir
    prefix = {"dom": "llm", "planner": "vision_planner", "tars": "vision_tars"}.get(body.target)
    if prefix is None:
        raise HTTPException(status_code=400, detail="target: dom | planner | tars")
    base = effective(data_dir, f"{prefix}_base_url").rstrip("/")
    key = effective(data_dir, f"{prefix}_api_key")
    model = effective(data_dir, f"{prefix}_model")
    if not base or not model:
        return {"ok": False, "detail": "Не заданы адрес или модель."}
    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{base}/chat/completions", json=payload,
                                         headers={"Authorization": f"Bearer {key or 'EMPTY'}"})
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:200] or 'нет соединения'}"}
    if response.status_code >= 400:
        return {"ok": False, "detail": f"HTTP {response.status_code}: {response.text[:200]}"}
    return {"ok": True, "detail": f"Модель {model} отвечает."}


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
        result = await run_task(text, settings=_settings(), download=body.download)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Tender search failed: {exc}") from exc
    return result.model_dump(mode="json")


@app.get(
    "/tenders",
    summary="Saved tenders from previous searches",
    operation_id="list_saved_tenders",
    description=(
        "Return tenders already found by the browser agent and stored in its database, newest first. "
        "Filter by platform host (e.g. zakupki.gov.ru), by text in title/customer/number, "
        "or by first_seen date (YYYY-MM-DD) to see what is new. Does not open any website."
    ),
)
def list_tenders(platform: str | None = None, q: str | None = None, since: str | None = None, limit: int = 50):
    from .storage.tenders import TenderStore

    settings = _settings()
    store = TenderStore(settings.db_url or settings.data_dir / "_agent" / "agent.sqlite3")
    return {"tenders": store.search(platform=platform, text=q, since=since, limit=max(1, min(limit, 500)))}
