"""High-level platform tools — composition depends on vision_mode."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from .... import config
from ...context import PlatformAgentContext
from ..tool_modes import TOOLS_HYBRID, normalize_vision_mode, tool_names_for_mode
from .dom import build_dom_tools
from .helpers import note_dom_blind
from .platform import build_platform_tools
from .screen import build_screen_tools

# Default hybrid set (backward-compatible export)
PLATFORM_TOOL_NAMES: tuple[str, ...] = TOOLS_HYBRID

# Test / internal back-compat
_note_dom_blind = note_dom_blind


def build_high_level_tools(ctx: PlatformAgentContext) -> list[StructuredTool]:
    """Build tools for ctx.vision_mode (dom|hybrid|vision)."""
    vision_mode = normalize_vision_mode(
        getattr(ctx, "vision_mode", None) or config.AGENT_VISION_MODE
    )
    ctx.vision_mode = vision_mode

    by_name: dict[str, StructuredTool] = {}
    by_name.update(build_platform_tools(ctx, vision_mode=vision_mode))
    by_name.update(build_dom_tools(ctx, vision_mode=vision_mode))
    by_name.update(build_screen_tools(ctx, vision_mode=vision_mode))

    order = tool_names_for_mode(vision_mode)
    missing = [n for n in order if n not in by_name]
    if missing:
        raise RuntimeError(f"tool builders missing for {missing}")
    return [by_name[n] for n in order]


__all__ = [
    "PLATFORM_TOOL_NAMES",
    "build_high_level_tools",
    "note_dom_blind",
    "_note_dom_blind",
]
