"""Tool name set for platform agent (DOM + adapters)."""

from __future__ import annotations

TOOLS_PLATFORM: tuple[str, ...] = (
    "open_platform_search",
    "list_new_cards",
    "open_tender",
    "list_tender_documents",
    "download_document",
    "save_overview",
    "mark_processed",
    "goto_next_page",
    "finish",
    "dom_snapshot",
    "click_element",
    "fill_element",
    "navigate",
)


def tool_names_for_mode() -> tuple[str, ...]:
    return TOOLS_PLATFORM
