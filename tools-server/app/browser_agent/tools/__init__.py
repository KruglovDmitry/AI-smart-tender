"""Platform agent tools package — one module per tool."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from . import (
    check_tender_seen,
    click_xy,
    collect_card_urls,
    download_url,
    extract_tender_id,
    filter_unseen_tenders,
    finish_platform_task,
    get_page_text,
    inspect_page_nav,
    list_download_links,
    mark_tender_seen,
    navigate,
    press_key,
    save_tender_overview,
    screenshot,
    scroll,
    type_text,
    wait,
)
from ._common import PlatformAgentContext, make_context
from .dedup_store import SeenTenderStore
from .tender_id import platform_from_url, resolve_tender_id

__all__ = [
    "PlatformAgentContext",
    "SeenTenderStore",
    "AGENT_TOOL_MODES",
    "build_langchain_tools",
    "make_context",
    "normalize_tools_mode",
    "platform_from_url",
    "resolve_tender_id",
]

# full — legacy + domain helpers; browser — low-level only; platform — adapter high-level tools
AGENT_TOOL_MODES = ("full", "browser", "platform")


def normalize_tools_mode(mode: str | None) -> str:
    m = (mode or "full").strip().lower()
    if m in {"primitive", "low", "browser_only", "raw"}:
        return "browser"
    if m in {"adapter", "high", "hl"}:
        return "platform"
    if m not in AGENT_TOOL_MODES:
        return "full"
    return m


def build_langchain_tools(
    ctx: PlatformAgentContext,
    mode: str | None = None,
) -> list[StructuredTool]:
    """Assemble StructuredTools; mode=browser excludes domain helpers."""
    mode = normalize_tools_mode(mode)

    if mode == "platform":
        from ...agent.tools import build_high_level_tools

        # High-level + finish_platform_task alias + wait/press for SPA
        tools = build_high_level_tools(ctx)
        tools.extend(
            [
                wait.make_tool(ctx),
                press_key.make_tool(ctx),
                scroll.make_tool(ctx),
                click_xy.make_tool(ctx),
                type_text.make_tool(ctx),
                finish_platform_task.make_tool(ctx),
            ]
        )
        # de-dupe by name (navigate/screenshot may appear twice)
        seen: set[str] = set()
        unique: list[StructuredTool] = []
        for t in tools:
            if t.name in seen:
                continue
            seen.add(t.name)
            unique.append(t)
        return unique

    browser_only = [
        navigate.make_tool(ctx),
        screenshot.make_tool(ctx),
        click_xy.make_tool(ctx),
        type_text.make_tool(ctx),
        press_key.make_tool(ctx),
        scroll.make_tool(ctx),
        wait.make_tool(ctx),
        get_page_text.make_tool(ctx),
        list_download_links.make_tool(ctx),
        download_url.make_tool(ctx),
        finish_platform_task.make_tool(ctx),
    ]
    if mode == "browser":
        return browser_only

    # full: legacy helpers + high-level adapter tools (prefer high-level when available)
    from ...agent.tools import build_high_level_tools

    high = build_high_level_tools(ctx)
    legacy = [
        navigate.make_tool(ctx),
        screenshot.make_tool(ctx),
        click_xy.make_tool(ctx),
        type_text.make_tool(ctx),
        press_key.make_tool(ctx),
        scroll.make_tool(ctx),
        wait.make_tool(ctx),
        get_page_text.make_tool(ctx),
        inspect_page_nav.make_tool(ctx),
        list_download_links.make_tool(ctx),
        download_url.make_tool(ctx),
        collect_card_urls.make_tool(ctx),
        extract_tender_id.make_tool(ctx),
        check_tender_seen.make_tool(ctx),
        filter_unseen_tenders.make_tool(ctx),
        save_tender_overview.make_tool(ctx),
        mark_tender_seen.make_tool(ctx),
        finish_platform_task.make_tool(ctx),
    ]
    # Prefer first occurrence (high-level names win for shared names like navigate)
    seen_names: set[str] = set()
    out: list[StructuredTool] = []
    for t in high + legacy:
        if t.name in seen_names:
            continue
        seen_names.add(t.name)
        out.append(t)
    return out
