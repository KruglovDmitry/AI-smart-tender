"""Thin FastAPI route handlers — validation + delegate to agent/domain/core."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import config
from ..platforms.base import CardRef
from ..platforms.registry import get_adapter
from .schemas import PlatformTaskBody, TenderDownloadBody

router = APIRouter()


@router.get("/health")
async def health():
    grounding = await _grounding_health()
    perception = await _perception_health()
    # Grounding required for hybrid/vision clicks; perception required only for vision mode
    grounding_ok = bool(grounding.get("ok") or not grounding.get("required"))
    status = "ok" if grounding_ok else "degraded"
    return {
        "status": status,
        "data_root": str(config.DATA_ROOT),
        "agent_llm_configured": bool(
            config.AGENT_LLM_BASE_URL and config.AGENT_LLM_API_KEY
        ),
        "primary_model": getattr(config, "AGENT_PRIMARY_MODEL", config.AGENT_LLM_MODEL),
        "vl_model": config.AGENT_VL_MODEL,
        "vision_mode_default": getattr(config, "AGENT_VISION_MODE", "hybrid"),
        "grounding": grounding,
        "perception": perception,
        # backward-compatible alias
        "vision": grounding,
        "platform_agent": {
            "model": config.AGENT_LLM_MODEL,
            "max_steps": config.PLATFORM_MAX_STEPS,
            "max_new_tenders": config.PLATFORM_MAX_NEW_TENDERS,
            "mode_default": getattr(config, "PLATFORM_AGENT_MODE", "platform"),
            "vision_backend": getattr(config, "AGENT_VISION_BACKEND", "ui_tars"),
            "perception_backend": getattr(config, "AGENT_PERCEPTION_BACKEND", "qwen_vl"),
            "vision_mode": getattr(config, "AGENT_VISION_MODE", "hybrid"),
        },
    }


async def _grounding_health() -> dict:
    """Probe grounding backend (where is X?)."""
    backend = getattr(config, "AGENT_VISION_BACKEND", "ui_tars")
    if backend == "ui_tars":
        from ..core.vision.ui_tars.client import UiTarsClient

        probe = await UiTarsClient().probe()
        return {
            "role": "grounding",
            "backend": "ui_tars",
            "required": True,
            "ok": bool(probe.get("ok")),
            "configured": bool(probe.get("configured")),
            "base_url": probe.get("base_url") or config.UI_TARS_BASE_URL or None,
            "model": probe.get("model") or config.UI_TARS_MODEL,
            "latency_ms": probe.get("latency_ms"),
            "note": probe.get("note"),
        }
    base = (config.AGENT_LLM_BASE_URL or "").rstrip("/")
    if not base or not config.AGENT_LLM_API_KEY:
        return {
            "role": "grounding",
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
            "role": "grounding",
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
            "role": "grounding",
            "backend": backend,
            "required": True,
            "ok": False,
            "configured": True,
            "base_url": base,
            "note": str(e)[:200],
        }


async def _perception_health() -> dict:
    """Probe perception backend (what is on screen?)."""
    backend = getattr(config, "AGENT_PERCEPTION_BACKEND", "qwen_vl")
    configured = bool(config.perception_configured())
    if backend == "ui_tars":
        from ..core.vision.ui_tars.client import UiTarsClient

        probe = await UiTarsClient().probe()
        return {
            "role": "perception",
            "backend": "ui_tars",
            "required": False,
            "ok": bool(probe.get("ok")),
            "configured": configured,
            "base_url": probe.get("base_url") or config.UI_TARS_BASE_URL or None,
            "model": probe.get("model") or config.UI_TARS_MODEL,
            "latency_ms": probe.get("latency_ms"),
            "note": probe.get("note"),
        }
    base = (getattr(config, "AGENT_PERCEPTION_BASE_URL", "") or "").rstrip("/")
    key = getattr(config, "AGENT_PERCEPTION_API_KEY", "") or ""
    model = getattr(config, "AGENT_PERCEPTION_MODEL", "qwen3-vl-plus")
    if not base or not key:
        return {
            "role": "perception",
            "backend": backend,
            "required": False,
            "ok": False,
            "configured": False,
            "model": model,
            "note": "AGENT_PERCEPTION_BASE_URL / API_KEY not set",
        }
    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(
                f"{base}/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
        return {
            "role": "perception",
            "backend": backend,
            "required": False,
            "ok": r.status_code < 400,
            "configured": True,
            "base_url": base,
            "model": model,
            "note": None if r.status_code < 400 else f"HTTP {r.status_code}",
        }
    except Exception as e:
        return {
            "role": "perception",
            "backend": backend,
            "required": False,
            "ok": False,
            "configured": True,
            "base_url": base,
            "model": model,
            "note": str(e)[:200],
        }


async def _vision_health() -> dict:
    """Backward-compatible alias → grounding."""
    return await _grounding_health()


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
            vision_mode=body.vision_mode,
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
    """
    Open one card and download docs.
    Direct adapter path when vision_mode omitted / dom-like happy path;
    agent loop when vision_mode is hybrid|vision (or EIS_TEST_NO_DOCS_ROUTE).
    """
    from datetime import datetime, timezone

    from ..agent.loop import _write_debug_json
    from ..agent.tools.tool_modes import normalize_vision_mode
    from ..core.browser.primitives import download_url, get_page_text
    from ..core.browser.session import browser_runtime
    from ..core.llm.usage import bind_usage, current_usage
    from ..domain import manifest as manifest_mod
    from ..domain import overview as overview_mod
    from ..domain.workspace import safe_tender_dirname, switch_downloads

    url = (body.tender_url or "").strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="tender_url must be http(s)")

    vmode_raw = body.vision_mode
    use_agent = False
    if vmode_raw is not None:
        vmode = normalize_vision_mode(vmode_raw)
        use_agent = vmode in {"hybrid", "vision"} or bool(
            getattr(config, "EIS_TEST_NO_DOCS_ROUTE", False)
        )
    elif getattr(config, "EIS_TEST_NO_DOCS_ROUTE", False):
        vmode = normalize_vision_mode(getattr(config, "AGENT_VISION_MODE", "hybrid"))
        use_agent = True
    else:
        vmode = None

    if use_agent:
        return await _run_tender_download_agent(body, vision_mode=vmode or "hybrid")

    bind_usage()
    t0 = time.perf_counter()
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
        overview_path = None
        overview_error = None
        overview_brief = None
        try:
            page = await get_page_text(rt, 12000)
            payload = await overview_mod.extract_overview(
                tender_id=tid,
                tender_url=step.url or url,
                page_url=str(page.get("url") or step.url or url),
                page_title=str(page.get("title") or ""),
                page_text=str(page.get("text") or ""),
                platform=host,
            )
            out_path = folder / "overview.json"
            out_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            manifest_mod.upsert_overview_fields(
                folder, {**payload, "platform": host, "tender_id": tid}
            )
            overview_path = str(out_path)
            overview_brief = {
                "object": payload.get("object"),
                "customer": payload.get("customer"),
                "price": payload.get("price"),
                "deadline": payload.get("deadline"),
            }
        except Exception as e:
            overview_error = str(e)

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
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + tid[:40]
        result = {
            "ok": True,
            "adapter": getattr(adapter, "display_name", host),
            "tender_id": tid,
            "url": step.url,
            "tender_dir": str(folder),
            "documents_found": len(docs),
            "downloaded": downloaded,
            "manifest": str(manifest_mod.manifest_path(folder)),
            "overview_path": overview_path,
            "overview": overview_brief,
            "overview_error": overview_error,
            "model": config.AGENT_PRIMARY_MODEL,
            "vision_mode": "adapter",
            "eis_test_no_docs_route": bool(
                getattr(config, "EIS_TEST_NO_DOCS_ROUTE", False)
            ),
            "usage": current_usage(),
            "wall_time_s": round(time.perf_counter() - t0, 1),
        }
        debug_path = _write_debug_json(run_id, result)
        if debug_path is not None:
            result["debug_json"] = str(debug_path)
        return result


async def _run_tender_download_agent(body: TenderDownloadBody, *, vision_mode: str):
    """Agent-driven single-tender download (hybrid/vision)."""
    from urllib.parse import urlparse

    from ..agent.loop import run_platform_task

    url = (body.tender_url or "").strip()
    host = (urlparse(url).netloc or "platform").replace("www.", "")
    session = body.download_subdir or (
        "".join(c if c.isalnum() or c in "-_" else "_" for c in host)[:80]
    )
    max_files = body.max_files
    instruction = (body.instruction or "").strip() or (
        f"Одна закупка: {url}. "
        f"open_tender(card_url) → save_overview → найди и скачай до {max_files} документов "
        f"(list_tender_documents/download_document или inspect_screen→click_target). "
        f"mark_processed → finish."
    )
    try:
        result = await run_platform_task(
            platform_url=url,
            keywords="tender-download",
            max_new_tenders=1,
            max_steps=body.max_steps or 40,
            download_subdir=session,
            instruction=instruction,
            vision_mode=vision_mode,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "ok": bool(result.get("success")),
        "agent": True,
        "vision_mode": result.get("vision_mode"),
        "mode_switches": result.get("mode_switches"),
        "eis_test_no_docs_route": result.get("eis_test_no_docs_route"),
        "tender_url": url,
        "downloaded_files": result.get("downloaded_files"),
        "downloaded_files_rel": result.get("downloaded_files_rel"),
        "processed_tenders": result.get("processed_tenders"),
        "summary": result.get("summary"),
        "steps": result.get("steps"),
        "trace": result.get("trace"),
        "usage": result.get("usage"),
        "wall_time_s": result.get("wall_time_s"),
        "debug_json": result.get("debug_json"),
        "model": result.get("model"),
        "tools": result.get("tools"),
    }
