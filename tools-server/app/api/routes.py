"""Thin FastAPI route handlers — validation + delegate to agent/domain/core."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import config
from ..platforms.base import CardRef
from ..platforms.registry import get_adapter
from .schemas import PlatformTaskBody, TenderDownloadBody

router = APIRouter()


@router.get("/health")
async def health():
    vision = await _vision_health()
    status = "ok" if vision.get("ok") or not vision.get("required") else "degraded"
    return {
        "status": status,
        "data_root": str(config.DATA_ROOT),
        "agent_llm_configured": bool(
            config.AGENT_LLM_BASE_URL and config.AGENT_LLM_API_KEY
        ),
        "primary_model": getattr(config, "AGENT_PRIMARY_MODEL", config.AGENT_LLM_MODEL),
        "vl_model": config.AGENT_VL_MODEL,
        "vision": vision,
        "platform_agent": {
            "model": config.AGENT_LLM_MODEL,
            "max_steps": config.PLATFORM_MAX_STEPS,
            "max_new_tenders": config.PLATFORM_MAX_NEW_TENDERS,
            "mode_default": getattr(config, "PLATFORM_AGENT_MODE", "platform"),
            "vision_backend": getattr(config, "AGENT_VISION_BACKEND", "ui_tars"),
        },
    }


async def _vision_health() -> dict:
    """Probe the configured vision backend so /health fails loudly if GPU/vLLM is down."""
    backend = getattr(config, "AGENT_VISION_BACKEND", "ui_tars")
    if backend == "ui_tars":
        from ..core.vision.ui_tars.client import UiTarsClient

        probe = await UiTarsClient().probe()
        return {
            "backend": "ui_tars",
            "required": True,
            "ok": bool(probe.get("ok")),
            "configured": bool(probe.get("configured")),
            "base_url": probe.get("base_url") or config.UI_TARS_BASE_URL or None,
            "model": probe.get("model") or config.UI_TARS_MODEL,
            "latency_ms": probe.get("latency_ms"),
            "note": probe.get("note"),
        }
    # qwen_vl uses the same OpenAI-compatible LLM gateway
    base = (config.AGENT_LLM_BASE_URL or "").rstrip("/")
    if not base or not config.AGENT_LLM_API_KEY:
        return {
            "backend": backend,
            "required": True,
            "ok": False,
            "configured": False,
            "note": "AGENT_LLM_BASE_URL / AGENT_LLM_API_KEY not set",
        }
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(
                f"{base}/v1/models",
                headers={"Authorization": f"Bearer {config.AGENT_LLM_API_KEY}"},
            )
        return {
            "backend": backend,
            "required": True,
            "ok": r.status_code < 400,
            "configured": True,
            "base_url": base,
            "model": config.AGENT_VL_MODEL,
            "note": None if r.status_code < 400 else f"HTTP {r.status_code}",
        }
    except Exception as e:
        return {
            "backend": backend,
            "required": True,
            "ok": False,
            "configured": True,
            "base_url": base,
            "note": str(e)[:200],
        }


@router.post("/run_platform_task")
async def run_platform_task_route(body: PlatformTaskBody):
    try:
        from ..agent.loop import run_platform_task

        return await run_platform_task(
            platform_url=body.platform_url,
            keywords=body.keywords,
            max_new_tenders=body.max_new_tenders,
            max_steps=body.max_steps,
            download_subdir=body.download_subdir,
            instruction=body.instruction,
            tools_mode=body.tools_mode,
        )
    except ImportError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Platform agent failed: {e}") from e


@router.post("/run_tender_download")
async def run_tender_download_route(body: TenderDownloadBody):
    """Open one card via adapter, collect docs, download into tenders/<host>/<id>/."""
    from ..core.browser.primitives import download_url
    from ..core.browser.session import browser_runtime
    from ..domain import manifest as manifest_mod
    from ..domain.workspace import safe_tender_dirname, switch_downloads

    url = (body.tender_url or "").strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="tender_url must be http(s)")

    adapter = get_adapter(url)
    host = getattr(adapter, "host", "platform")
    if host == "*":
        from urllib.parse import urlparse

        host = (urlparse(url).netloc or "platform").replace("www.", "")
    session = "".join(c if c.isalnum() or c in "-_" else "_" for c in host)[:80]
    root = config.DATA_ROOT / "tenders" / (body.download_subdir or session)
    root.mkdir(parents=True, exist_ok=True)

    async with browser_runtime(downloads_dir=root) as rt:
        card = CardRef(url=url, tender_id=adapter.tender_id(url))
        step = await adapter.open_card(rt, card)
        if not step.ok:
            raise HTTPException(status_code=502, detail=step.note or "open_card failed")
        tid = str((step.data or {}).get("tender_id") or card.tender_id or "unknown")
        folder = root / safe_tender_dirname(tid)
        switch_downloads(rt, folder)
        manifest_mod.ensure_manifest(
            folder, tender_id=tid, platform=host, tender_url=step.url or url
        )
        docs = await adapter.collect_documents(rt)
        downloaded = []
        for doc in docs[: body.max_files]:
            res = await download_url(rt, doc.url, suggested_name=doc.name)
            if res.get("ok") and not res.get("skipped"):
                manifest_mod.append_file(
                    folder,
                    name=Path(str(res.get("file") or doc.name)).name,
                    sha256=str(res.get("sha256") or ""),
                    bytes_count=int(res.get("bytes") or 0),
                    source_url=str(res.get("source_url") or doc.url),
                    content_type=str(res.get("content_type") or ""),
                    tender_id=tid,
                    platform=host,
                    tender_url=step.url or url,
                )
                downloaded.append(res)
        return {
            "ok": True,
            "adapter": getattr(adapter, "display_name", host),
            "tender_id": tid,
            "url": step.url,
            "tender_dir": str(folder),
            "documents_found": len(docs),
            "downloaded": downloaded,
            "manifest": str(manifest_mod.manifest_path(folder)),
        }
