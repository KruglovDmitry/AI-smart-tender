"""TENAG dashboard BFF: static UI + /data scan + proxy to tools-server."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import data_scan

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data")).resolve()
TOOLS_SERVER_URL = os.getenv("TOOLS_SERVER_URL", "http://tools-server:8000").rstrip("/")
SEEN_DB = Path(
    os.getenv("SEEN_TENDERS_DB", str(DATA_ROOT / "_state" / "seen_tenders.sqlite3"))
)
# Long agent runs — keep proxy open
PROXY_TIMEOUT = httpx.Timeout(connect=30.0, read=1800.0, write=60.0, pool=30.0)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="TENAG Dashboard", version="1.0.0")


class PlatformRunBody(BaseModel):
    platform_url: str
    keywords: str
    max_new_tenders: int = Field(default=2, ge=1, le=50)
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
    return {
        "dashboard": "ok",
        "data_root": str(DATA_ROOT),
        "tenders_count": len(tenders),
        "files_count": data_scan.count_tender_files(tenders),
        "seen_tenders_count": seen,
        "agent_llm_configured": bool(
            tools_health and tools_health.get("agent_llm_configured")
        ),
        "tools_server": {
            "url": TOOLS_SERVER_URL,
            "ok": tools_health is not None,
            "error": tools_error,
            "health": tools_health,
        },
        "activity": data_scan.recent_agent_logs(DATA_ROOT, limit=20),
    }


@app.get("/api/tenders")
async def api_tenders(limit: int = 200):
    limit = max(1, min(limit, 500))
    tenders = data_scan.scan_tenders(DATA_ROOT, limit=limit)
    return {"count": len(tenders), "tenders": tenders}


@app.post("/api/run/platform")
async def api_run_platform(body: PlatformRunBody):
    payload = body.model_dump(exclude_none=True)
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT) as client:
            r = await client.post(
                f"{TOOLS_SERVER_URL}/run_platform_task", json=payload
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"tools-server unreachable: {e}") from e
    return _proxy_json(r)


@app.post("/api/run/tender")
async def api_run_tender(body: TenderDownloadRunBody):
    payload = body.model_dump(exclude_none=True)
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT) as client:
            r = await client.post(
                f"{TOOLS_SERVER_URL}/run_tender_download", json=payload
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"tools-server unreachable: {e}") from e
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


@app.api_route("/api/proxy/{path:path}", methods=["GET", "POST"])
async def generic_proxy(path: str, request: Request):
    """Optional thin proxy (e.g. download_file)."""
    url = f"{TOOLS_SERVER_URL}/{path}"
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT) as client:
            body = await request.body()
            r = await client.request(
                request.method,
                url,
                content=body,
                params=dict(request.query_params),
                headers={
                    k: v
                    for k, v in request.headers.items()
                    if k.lower() in {"content-type", "accept"}
                },
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return _proxy_json(r)
