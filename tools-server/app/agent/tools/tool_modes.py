"""Tool name set for platform agent (DOM + adapters; no screen/vision)."""

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

# Back-compat aliases used by older imports/tests
TOOLS_DOM = TOOLS_PLATFORM
TOOLS_HYBRID = TOOLS_PLATFORM
TOOLS_BY_VISION_MODE: dict[str, tuple[str, ...]] = {
    "dom": TOOLS_PLATFORM,
    "hybrid": TOOLS_PLATFORM,
    "platform": TOOLS_PLATFORM,
}


def normalize_vision_mode(mode: str | None, default: str = "platform") -> str:
    """Deprecated: vision modes removed; always resolve to platform."""
    _ = (mode or default or "platform").strip().lower()
    return "platform"


def tool_names_for_mode(
    vision_mode: str | None = None, default: str = "platform"
) -> tuple[str, ...]:
    _ = normalize_vision_mode(vision_mode, default=default)
    return TOOLS_PLATFORM
