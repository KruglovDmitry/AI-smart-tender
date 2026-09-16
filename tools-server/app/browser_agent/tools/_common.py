"""Shared context and helpers for platform agent tools."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
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
    if config.AGENT_DEBUG_LOGS and name == "vl_analyze_screenshot":
        advice = ""
        if isinstance(result, dict):
            advice = str(result.get("advice") or result.get("error") or "")
        logger.info(
            "VL (%s):\n%s",
            (result.get("model") if isinstance(result, dict) else None)
            or config.AGENT_VL_MODEL,
            advice[:3000] + ("..." if len(advice) > 3000 else ""),
        )


def make_context(
    rt: Any,
    store: SeenTenderStore,
    platform_url: str,
    keywords: str,
    max_new_tenders: int,
    task_hint: str = "",
) -> PlatformAgentContext:
    return PlatformAgentContext(
        rt=rt,
        store=store,
        platform=platform_from_url(platform_url),
        keywords=keywords.strip(),
        max_new_tenders=max(1, max_new_tenders),
        task_hint=task_hint,
    )
