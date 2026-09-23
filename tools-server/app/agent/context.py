"""Shared context and helpers for platform agent tools."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config
from ..domain.dedup import SeenTenderStore
from ..domain.tender_id import platform_from_url
from ..domain.workspace import safe_tender_dirname, switch_downloads

logger = logging.getLogger(__name__)


@dataclass
class PlatformAgentContext:
    """Mutable runtime state shared by LangChain tools."""

    rt: Any
    store: SeenTenderStore
    platform: str
    keywords: str
    max_new_tenders: int
    task_hint: str = ""
    downloads_root: Path | None = None
    current_tender_id: str | None = None
    current_tender_url: str | None = None
    current_tender_dir: Path | None = None
    new_tenders_processed: int = 0
    processed_tenders: list[dict[str, Any]] = field(default_factory=list)
    downloaded_urls: set[str] = field(default_factory=set)
    # Session notes: what we learned about THIS platform in the current run
    platform_notes: dict[str, Any] = field(default_factory=dict)
    done: bool = False
    final_summary: str = ""
    final_success: bool = False
    trace: list[dict[str, Any]] = field(default_factory=list)
    vision_run_id: str = ""
    inject_screenshots: bool = False


def note_results_url(ctx: PlatformAgentContext, url: str | None) -> None:
    u = (url or "").strip()
    if u.startswith("http"):
        ctx.platform_notes["results_url"] = u


def note_pending_new(ctx: PlatformAgentContext, items: list[dict[str, Any]]) -> None:
    slim = []
    for it in items or []:
        tid = str(it.get("tender_id") or "").strip()
        turl = str(it.get("tender_url") or "").strip()
        if tid and turl:
            slim.append({"tender_id": tid, "tender_url": turl})
    ctx.platform_notes["pending_new"] = slim
    ctx.platform_notes["pending_new_count"] = len(slim)


def pop_pending_new(ctx: PlatformAgentContext, tender_id: str) -> None:
    pending = list(ctx.platform_notes.get("pending_new") or [])
    tid = (tender_id or "").strip()
    ctx.platform_notes["pending_new"] = [x for x in pending if x.get("tender_id") != tid]
    ctx.platform_notes["pending_new_count"] = len(ctx.platform_notes["pending_new"])


def platform_notes_digest(ctx: PlatformAgentContext) -> str:
    n = ctx.platform_notes or {}
    lines = ["NOTES (сессия, эта площадка):"]
    if n.get("results_url"):
        lines.append(f"- results_url={n['results_url']}")
    pending = n.get("pending_new") or []
    if pending:
        lines.append(f"- pending_new ({len(pending)}):")
        for it in pending[:8]:
            lines.append(f"  • {it.get('tender_id')}: {it.get('tender_url')}")
    else:
        lines.append("- pending_new: []")
    if n.get("pagination_hint"):
        lines.append(f"- pagination_hint={n['pagination_hint']}")
    if n.get("suggested_next_url"):
        lines.append(f"- suggested_next_url={n['suggested_next_url']}")
    if n.get("docs_entry_hint"):
        lines.append(f"- docs_entry_hint={n['docs_entry_hint']}")
    lines.append(
        "Правило: navigate ТОЛЬКО по exact tender_url из pending_new/new[]; "
        "пагинация — inspect_page_nav → suggested_next_url / next.href. "
        "При 404 — вернись на results_url."
    )
    return "\n".join(lines)


def to_json(result: dict[str, Any]) -> str:
    # Короче observation → меньше токенов в истории LLM
    return json.dumps(result, ensure_ascii=False, default=str)[:12000]


def trace(ctx: PlatformAgentContext, name: str, args: dict[str, Any], result: Any) -> None:
    ctx.trace.append({"tool": name, "args": args, "result": result})
    if config.AGENT_DEBUG_LOGS:
        try:
            from .logging import log_tool_step

            log_tool_step(name, args, result)
        except Exception:
            logger.debug("log_tool_step failed", exc_info=True)


def make_context(
    rt: Any,
    store: SeenTenderStore,
    platform_url: str,
    keywords: str,
    max_new_tenders: int,
    task_hint: str = "",
    downloads_root: Path | None = None,
) -> PlatformAgentContext:
    root = Path(downloads_root) if downloads_root else Path(rt.downloads_dir)
    root.mkdir(parents=True, exist_ok=True)
    return PlatformAgentContext(
        rt=rt,
        store=store,
        platform=platform_from_url(platform_url),
        keywords=keywords.strip(),
        max_new_tenders=max(1, max_new_tenders),
        task_hint=task_hint,
        downloads_root=root,
    )


def ensure_tender_workspace(
    ctx: PlatformAgentContext,
    tender_id: str,
    tender_url: str | None = None,
) -> Path:
    """Create session_root/<tender_id>/ and point rt.downloads_dir there."""
    root = ctx.downloads_root
    if root is None:
        root = Path(ctx.rt.downloads_dir)
        ctx.downloads_root = root
    folder = Path(root) / safe_tender_dirname(tender_id)
    switch_downloads(ctx.rt, folder)
    ctx.current_tender_id = tender_id
    if tender_url:
        ctx.current_tender_url = tender_url
    ctx.current_tender_dir = folder
    return folder
