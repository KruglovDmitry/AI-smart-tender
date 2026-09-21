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
    "build_langchain_tools",
    "make_context",
    "platform_from_url",
    "resolve_tender_id",
]


def build_langchain_tools(ctx: PlatformAgentContext) -> list[StructuredTool]:
    """Assemble all StructuredTools bound to the current context."""
    return [
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
