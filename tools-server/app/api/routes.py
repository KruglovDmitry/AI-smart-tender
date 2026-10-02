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
    """
    Open one card and download docs.
    Default: direct adapter path. Agent loop when use_agent=true or EIS_TEST_NO_DOCS_ROUTE.
    """
    from datetime import datetime, timezone

    from ..agent.loop import _write_debug_json
    from ..core.browser.primitives import download_url, get_page_text
    from ..core.browser.session import browser_runtime
    from ..core.llm.usage import bind_usage, current_usage
    from ..domain import manifest as manifest_mod
    from ..domain import overview as overview_mod
    from ..domain.workspace import safe_tender_dirname, switch_downloads

    url = (body.tender_url or "").strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="tender_url must be http(s)")

    use_agent = bool(body.use_agent) or bool(
        getattr(config, "EIS_TEST_NO_DOCS_ROUTE", False)
    )
    if use_agent:
        return await _run_tender_download_agent(body)

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
            overview_path = str(out_path)
            overview_brief = {
                "object": payload.get("object"),
                "customer": payload.get("customer"),
                "price": payload.get("price"),
                "deadline": payload.get("deadline"),
            }
            try:
                manifest_mod.upsert_overview_fields(
                    folder, {**payload, "platform": host, "tender_id": tid}
                )
            except Exception:
                pass
        except Exception as e:
            overview_error = str(e)

        docs = await adapter.collect_documents(rt)
        downloaded: list[dict] = []
        for doc in docs[: body.max_files]:
            res = await download_url(rt, doc.url, suggested_name=doc.name)
            if res.get("ok"):
                downloaded.append(res)
                try:
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
                except Exception:
                    pass

        result = {
            "ok": True,
            "agent": False,
            "path": "adapter",
            "tender_id": tid,
            "tender_url": step.url or url,
            "tender_dir": str(folder),
            "adapter": getattr(adapter, "display_name", host),
            "overview_path": overview_path,
            "overview_error": overview_error,
            "overview": overview_brief,
            "documents_found": len(docs),
            "downloaded": downloaded,
            "downloaded_count": len(downloaded),
            "usage": current_usage(),
            "wall_time_s": round(time.perf_counter() - t0, 1),
        }
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-tdl"
        debug_path = _write_debug_json(run_id, result)
        if debug_path is not None:
            result["debug_json"] = str(debug_path)
        return result


async def _run_tender_download_agent(body: TenderDownloadBody):
    """Agent-driven single-tender download (DOM/platform tools)."""
    from urllib.parse import urlparse

    from ..agent.loop import run_platform_task

    url = (body.tender_url or "").strip()
    host = (urlparse(url).netloc or "platform").replace("www.", "")
    session = body.download_subdir or (
        "".join(c if c.isalnum() or c in "-_" else "_" for c in host)[:80]
    )
    max_files = body.max_files
    instruction = (body.instruction or "").strip() or (
        f"Одна закупка exact URL: {url}. "
        f"Сразу open_tender(card_url). Без поиска. "
        f"save_overview → list_tender_documents → download (до {max_files}). "
        f"Если list пуст: dom_snapshot → click_element(вкладка документов) → list снова. "
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
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "ok": bool(result.get("success")),
        "agent": True,
        "path": "agent",
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
        "eis_test_no_docs_route": result.get("eis_test_no_docs_route"),
    }
