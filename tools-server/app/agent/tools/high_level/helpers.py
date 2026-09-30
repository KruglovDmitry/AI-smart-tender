"""Shared helpers for high-level tools."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from ....domain import manifest as manifest_mod
from ....platforms.registry import get_adapter
from ...context import PlatformAgentContext


def adapter_for(ctx: PlatformAgentContext):
    url = ""
    try:
        url = ctx.rt.page.url or ""
    except Exception:
        url = ""
    seed = url or f"https://{ctx.platform}/"
    return get_adapter(seed)


async def persist_download(
    ctx: PlatformAgentContext, download_info: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Move Playwright download into tender folder and append manifest."""
    if not download_info:
        return None
    src = Path(str(download_info.get("file") or ""))
    suggested = str(
        download_info.get("suggested_name") or (src.name if src.name else "download.bin")
    )
    folder = ctx.current_tender_dir
    if folder is None:
        folder = Path(getattr(ctx.rt, "downloads_dir", None) or ".")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / suggested
    n = 1
    while dest.exists():
        stem, suf = Path(suggested).stem, Path(suggested).suffix
        dest = folder / f"{stem}_{n}{suf}"
        n += 1
    try:
        if src.exists():
            shutil.move(str(src), str(dest))
        else:
            return download_info
    except Exception:
        return download_info
    data = dest.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    source_url = str(download_info.get("source_url") or "")
    if ctx.current_tender_dir is not None:
        try:
            manifest_mod.append_file(
                ctx.current_tender_dir,
                name=dest.name,
                sha256=sha,
                bytes_count=len(data),
                source_url=source_url,
                content_type="",
                tender_id=ctx.current_tender_id or "",
                platform=ctx.platform,
                tender_url=ctx.current_tender_url or "",
            )
        except Exception:
            pass
    files = getattr(ctx.rt, "downloaded_files", None)
    if isinstance(files, list):
        files.append(str(dest))
    return {
        "file": str(dest),
        "bytes": len(data),
        "sha256": sha,
        "source_url": source_url,
    }


def note_dom_blind(
    ctx: PlatformAgentContext, tool: str, result: dict[str, Any]
) -> None:
    """Track DOM-empty → vision-helped streak for auto hybrid→vision."""
    empty_dom = False
    if tool in {"dom_snapshot", "list_tender_documents", "list_new_cards"}:
        if not result.get("ok"):
            empty_dom = True
        elif tool == "dom_snapshot" and not (result.get("elements") or []):
            empty_dom = True
        elif tool == "list_tender_documents" and int(result.get("count") or 0) == 0:
            empty_dom = True
        elif tool == "list_new_cards" and not (result.get("new") or []):
            empty_dom = True
    if empty_dom:
        ctx.platform_notes["_last_dom_empty"] = True
        return
    if tool in {"click_target", "click_on_screen", "type_into_target"}:
        if ctx.platform_notes.pop("_last_dom_empty", False) and result.get("changed"):
            tid = str(result.get("target_id") or "")
            if tool == "click_on_screen" or tid.startswith("v"):
                ctx.dom_blind_streak += 1
                return
        ctx.dom_blind_streak = 0


# Back-compat alias used by tests
_note_dom_blind = note_dom_blind
_adapter = adapter_for
_persist_download = persist_download
