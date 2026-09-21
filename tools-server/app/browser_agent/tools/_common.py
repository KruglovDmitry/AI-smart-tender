"""Shared context and helpers for platform agent tools."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ... import config
from .dedup_store import SeenTenderStore
from .tender_id import platform_from_url

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
    done: bool = False
    final_summary: str = ""
    final_success: bool = False
    trace: list[dict[str, Any]] = field(default_factory=list)


def to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)[:20000]


def trace(ctx: PlatformAgentContext, name: str, args: dict[str, Any], result: Any) -> None:
    ctx.trace.append({"tool": name, "args": args, "result": result})
    if config.AGENT_DEBUG_LOGS:
        try:
            from ..logging import log_tool_step

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


def safe_tender_dirname(tender_id: str) -> str:
    tid = (tender_id or "").strip() or "unknown"
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in tid)
    return cleaned[:120] or "unknown"


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
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    folder = root / safe_tender_dirname(tender_id)
    folder.mkdir(parents=True, exist_ok=True)

    ctx.current_tender_id = tender_id
    if tender_url:
        ctx.current_tender_url = tender_url
    ctx.current_tender_dir = folder
    ctx.rt.downloads_dir = folder
    return folder
