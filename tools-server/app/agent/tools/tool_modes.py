"""Tool name sets per vision_mode (dom | hybrid | vision)."""

from __future__ import annotations

VISION_MODES = ("dom", "hybrid", "vision")

# Table 6.2 from the vision↔primary brief
TOOLS_DOM: tuple[str, ...] = (
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
    "inspect_screen",
    "click_target",
)

TOOLS_HYBRID: tuple[str, ...] = (
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
    "inspect_screen",
    "click_target",
    "type_into_target",
    "click_on_screen",
)

TOOLS_VISION: tuple[str, ...] = (
    "open_platform_search",
    "open_tender",
    "save_overview",
    "mark_processed",
    "goto_next_page",
    "finish",
    "navigate",
    "inspect_screen",
    "click_target",
    "type_into_target",
    "scroll",
    "click_on_screen",
)

TOOLS_BY_VISION_MODE: dict[str, tuple[str, ...]] = {
    "dom": TOOLS_DOM,
    "hybrid": TOOLS_HYBRID,
    "vision": TOOLS_VISION,
}


def normalize_vision_mode(mode: str | None, default: str = "hybrid") -> str:
    m = (mode or default or "hybrid").strip().lower()
    if m not in VISION_MODES:
        raise ValueError(
            f"Unknown vision_mode={mode!r}. Allowed: dom | hybrid | vision"
        )
    return m


def tool_names_for_mode(vision_mode: str | None, default: str = "hybrid") -> tuple[str, ...]:
    mode = normalize_vision_mode(vision_mode, default=default)
    return TOOLS_BY_VISION_MODE[mode]
