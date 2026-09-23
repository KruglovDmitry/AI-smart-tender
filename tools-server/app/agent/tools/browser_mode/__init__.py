"""Browser ablation toolset — low-level only; not used by platform mode."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from . import (
    click_xy,
    download_url,
    finish,
    get_page_text,
    list_download_links,
    navigate,
    press_key,
    screenshot,
    scroll,
    type_text,
    wait,
)
from ...context import PlatformAgentContext

BROWSER_TOOL_NAMES: tuple[str, ...] = (
    "navigate",
    "screenshot",
    "click_xy",
    "type_text",
    "press_key",
    "scroll",
    "wait",
    "get_page_text",
    "list_download_links",
    "download_url",
    "finish",
)


def build_browser_tools(ctx: PlatformAgentContext) -> list[StructuredTool]:
    return [
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
        finish.make_tool(ctx),
    ]
