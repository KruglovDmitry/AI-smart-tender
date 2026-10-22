"""TENAG dashboard BFF: static UI + /data scan + proxy to tools-server."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import data_scan, db

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data")).resolve()
TOOLS_SERVER_URL = os.getenv("TOOLS_SERVER_URL", "http://tools-server:8000").rstrip("/")
AGENT_URL = os.getenv("TENDER_AGENT_URL", "http://tender-agent:8010").rstrip("/")
SEEN_DB = Path(
    os.getenv("SEEN_TENDERS_DB", str(DATA_ROOT / "_state" / "seen_tenders.sqlite3"))
)
# Long agent runs — keep proxy open
PROXY_TIMEOUT = httpx.Timeout(connect=30.0, read=1800.0, write=60.0, pool=30.0)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="TENAG Dashboard", version="1.0.0")


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """Страница и статика всегда перепроверяются: иначе после обновления браузер держит старый app.js."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class PlatformRunBody(BaseModel):
    platform_url: str
    keywords: str
    max_new_tenders: int = Field(default=5, ge=1, le=50)
    download_documents: bool = False
    download_subdir: str | None = None
    max_steps: int | None = Field(default=None, ge=5, le=200)
    instruction: str | None = None
    tools_mode: str | None = None


class TenderDownloadRunBody(BaseModel):
    tender_url: str
    max_files: int = Field(default=5, ge=1, le=30)
    download_subdir: str | None = None
    use_agent: bool = False
    max_steps: int | None = Field(default=None, ge=5, le=200)
    instruction: str | None = None


@app.get("/api/health")
async def api_health():
    return {"status": "ok", "service": "dashboard", "data_root": str(DATA_ROOT)}


@app.get("/api/status")
async def api_status():
    tenders = data_scan.scan_tenders(DATA_ROOT)
    tools_health: dict | None = None
    tools_error: str | None = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            r = await client.get(f"{TOOLS_SERVER_URL}/health")
            r.raise_for_status()
            tools_health = r.json()
    except Exception as e:
        tools_error = str(e)

    seen = data_scan.seen_tenders_count(SEEN_DB)
    agent_health: dict | None = None
    agent_error: str | None = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            r = await client.get(f"{AGENT_URL}/health")
            r.raise_for_status()
            agent_health = r.json()
    except Exception as e:
        agent_error = str(e)
    return {
        "dashboard": "ok",
        "data_root": str(DATA_ROOT),
        "tenders_count": len(tenders),
        "files_count": data_scan.count_tender_files(tenders),
        "seen_tenders_count": seen,
        "agent_llm_configured": bool(agent_health and agent_health.get("llm_configured")),
        "tools_server": {
            "url": TOOLS_SERVER_URL,
            "ok": tools_health is not None,
            "error": tools_error,
            "health": tools_health,
        },
        "tender_agent": {
            "url": AGENT_URL,
            "ok": agent_health is not None,
            "error": agent_error,
            "health": agent_health,
        },
        "activity": data_scan.recent_agent_logs(DATA_ROOT, limit=20),
        "database": _database_status(),
    }


# ---------- база закупок (Postgres tender-agent) ----------


def _db_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except db.DatabaseNotReady as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/db/summary")
def api_db_summary():
    return _db_call(db.summary)


@app.get("/api/db/tenders")
def api_db_tenders(
    platform: str | None = None,
    q: str | None = None,
    new_days: int | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    verified_only: bool = False,
    deleted: bool = False,
    sort: str = "published",
    limit: int = 50,
    offset: int = 0,
):
    return _db_call(
        db.list_tenders,
        platform=platform, q=q, new_days=new_days, published_from=published_from, published_to=published_to,
        min_price=min_price, max_price=max_price, verified_only=verified_only, deleted=deleted, sort=sort,
        limit=max(1, min(limit, 500)), offset=max(0, offset),
    )


@app.delete("/api/db/tenders/{tender_id}")
def api_db_tender_delete(tender_id: int):
    """Мягкое удаление: закупка скрывается из списков, документы и история прогонов остаются."""
    if not _db_call(db.set_deleted, tender_id, True):
        raise HTTPException(status_code=404, detail="Закупка не найдена")
    return {"id": tender_id, "deleted": True}


@app.post("/api/db/tenders/{tender_id}/restore")
def api_db_tender_restore(tender_id: int):
    if not _db_call(db.set_deleted, tender_id, False):
        raise HTTPException(status_code=404, detail="Закупка не найдена")
    return {"id": tender_id, "deleted": False}


@app.get("/api/db/tenders/{tender_id}")
def api_db_tender(tender_id: int):
    item = _db_call(db.tender_detail, tender_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Закупка не найдена")
    return item


@app.get("/api/db/documents/{document_id}/file")
def api_db_document_file(document_id: int):
    """Скачанный агентом файл — открыть с диска. У части площадок (Росатом) у файлов нет своих адресов,
    ссылка источника ведёт на карточку, а не на файл. Отдаём только файлы из папки данных."""
    found = _db_call(db.document_path, document_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Файл не скачан")
    path = Path(found[0]).resolve()
    if not path.is_relative_to(DATA_ROOT) or not path.is_file():
        raise HTTPException(status_code=404, detail="Файла нет в папке данных")
    return FileResponse(path, filename=path.name, content_disposition_type="inline")


@app.get("/api/db/methods")
def api_db_methods(days: int = 30, platform: str | None = None):
    return _db_call(db.methods_summary, days=max(1, min(days, 365)), platform=platform)


@app.get("/api/db/runs")
def api_db_runs(platform: str | None = None, limit: int = 50):
    return {"runs": _db_call(db.list_runs, platform=platform, limit=max(1, min(limit, 500)))}


# ---------- настройки агента (вкладка «Агент»): владелец — tender-agent, дашборд проксирует ----------


async def _agent_call(method: str, path: str, json: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0)) as client:
            r = await client.request(method, f"{AGENT_URL}{path}", json=json)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"tender-agent недоступен: {exc}") from exc
    try:
        data = r.json()
    except ValueError:
        data = {"detail": r.text[:300]}
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=data.get("detail", f"HTTP {r.status_code}"))
    return data


class AgentSettingsBody(BaseModel):
    values: dict = Field(default_factory=dict)


class AgentCheckBody(BaseModel):
    target: str


@app.get("/api/agent/settings")
async def api_agent_settings():
    return await _agent_call("GET", "/settings")


@app.put("/api/agent/settings")
async def api_agent_settings_save(body: AgentSettingsBody):
    return await _agent_call("PUT", "/settings", {"values": body.values})


@app.post("/api/agent/settings/check")
async def api_agent_settings_check(body: AgentCheckBody):
    return await _agent_call("POST", "/settings/check", {"target": body.target})


def _database_status() -> dict:
    try:
        return {"ok": True, "tenders_total": db.summary()["tenders_total"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


@app.get("/api/tenders")
async def api_tenders(limit: int = 200):
    limit = max(1, min(limit, 500))
    tenders = data_scan.scan_tenders(DATA_ROOT, limit=limit)
    return {"count": len(tenders), "tenders": tenders}


@app.post("/api/run/platform")
async def api_run_platform(body: PlatformRunBody):
    task = (
        f"С площадки {body.platform_url} верни последние {body.max_new_tenders} "
        f'закупок по ключевому слову "{body.keywords}"'
        + (" и скачай документы." if body.download_documents else ".")
    )
    if body.instruction:
        task = f"{task} {body.instruction}"
    return await _search(task)


@app.post("/api/run/tender")
async def api_run_tender(body: TenderDownloadRunBody):
    extra = f" {body.instruction}" if body.instruction else ""
    task = f'Открой {body.tender_url} и скачай документы по ключевому слову "документы".{extra}'
    return await _search(task)


async def _search(task: str):
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT) as client:
            r = await client.post(f"{AGENT_URL}/search_tenders", json={"task": task})
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"tender-agent unreachable: {e}") from e
    return _proxy_json(r)


def _proxy_json(r: httpx.Response):
    try:
        data = r.json()
    except Exception:
        data = {"detail": r.text[:2000] or f"HTTP {r.status_code}"}
    if r.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else data
        raise HTTPException(status_code=r.status_code, detail=detail)
    return data


@app.get("/")
async def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="index.html missing")
    return FileResponse(index_path)


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
