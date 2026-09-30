"""Agent tools — platform mode; composition by vision_mode."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from ...domain.dedup import SeenTenderStore
from ...domain.tender_id import platform_from_url, resolve_tender_id
from ..context import PlatformAgentContext, make_context
from .high_level import PLATFORM_TOOL_NAMES, build_high_level_tools
from .tool_modes import (
    TOOLS_BY_VISION_MODE,
    TOOLS_DOM,
    TOOLS_HYBRID,
    TOOLS_VISION,
    normalize_vision_mode,
    tool_names_for_mode,
)

__all__ = [
    "PlatformAgentContext",
    "SeenTenderStore",
    "AGENT_TOOL_MODES",
    "PLATFORM_TOOL_NAMES",
    "TOOLS_BY_VISION_MODE",
    "TOOLS_DOM",
    "TOOLS_HYBRID",
    "TOOLS_VISION",
    "build_langchain_tools",
    "make_context",
    "normalize_tools_mode",
    "normalize_vision_mode",
    "tool_names_for_mode",
    "platform_from_url",
    "resolve_tender_id",
]

# tools_mode is always platform now; browser ablation removed (use vision_mode=vision).
AGENT_TOOL_MODES = ("platform",)


def normalize_tools_mode(mode: str | None) -> str:
    m = (mode or "platform").strip().lower()
    if m == "full":
        raise ValueError(
            "tools_mode='full' удалён. Используй platform + vision_mode."
        )
    if m in {"browser", "primitive", "low", "browser_only", "raw"}:
        raise ValueError(
            "tools_mode='browser' удалён. Используй vision_mode='vision' "
            "(работа через экран без DOM-инструментов)."
        )
    if m in {"adapter", "high", "hl", "", "platform"}:
        return "platform"
    if m not in AGENT_TOOL_MODES:
        raise ValueError(f"Unknown tools_mode={mode!r}. Allowed: platform")
    return m


def build_langchain_tools(
    ctx: PlatformAgentContext,
    mode: str | None = None,
    vision_mode: str | None = None,
) -> list[StructuredTool]:
    normalize_tools_mode(mode)
    if vision_mode is not None:
        ctx.vision_mode = normalize_vision_mode(vision_mode)
    return build_high_level_tools(ctx)
