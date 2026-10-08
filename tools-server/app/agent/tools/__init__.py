"""Agent tools — platform mode (DOM + adapters)."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from ...domain.dedup import SeenTenderStore
from ...domain.tender_id import platform_from_url, resolve_tender_id
from ..context import PlatformAgentContext, make_context
from .dom import build_dom_tools
from .platform import build_platform_tools
from .tool_modes import TOOLS_PLATFORM, tool_names_for_mode

__all__ = [
    "PlatformAgentContext",
    "SeenTenderStore",
    "AGENT_TOOL_MODES",
    "PLATFORM_TOOL_NAMES",
    "TOOLS_PLATFORM",
    "build_langchain_tools",
    "build_tools",
    "make_context",
    "normalize_tools_mode",
    "tool_names_for_mode",
    "platform_from_url",
    "resolve_tender_id",
]

AGENT_TOOL_MODES = ("platform",)
PLATFORM_TOOL_NAMES: tuple[str, ...] = TOOLS_PLATFORM


def normalize_tools_mode(mode: str | None) -> str:
    m = (mode or "platform").strip().lower()
    if m == "full":
        raise ValueError("tools_mode='full' удалён. Используй platform.")
    if m in {"browser", "primitive", "low", "browser_only", "raw", "vision"}:
        raise ValueError(
            "tools_mode browser/vision удалён. Используй tools_mode='platform'."
        )
    if m in {"adapter", "high", "hl", "", "platform", "dom", "hybrid"}:
        return "platform"
    if m not in AGENT_TOOL_MODES:
        raise ValueError(f"Unknown tools_mode={mode!r}. Allowed: platform")
    return m


def build_tools(ctx: PlatformAgentContext) -> list[StructuredTool]:
    """Compose adapter + DOM tools in PLATFORM order."""
    by_name: dict[str, StructuredTool] = {}
    by_name.update(build_platform_tools(ctx))
    by_name.update(build_dom_tools(ctx))
    order = tool_names_for_mode()
    missing = [n for n in order if n not in by_name]
    if missing:
        raise RuntimeError(f"tool builders missing for {missing}")
    return [by_name[n] for n in order]


def build_langchain_tools(
    ctx: PlatformAgentContext,
    mode: str | None = None,
) -> list[StructuredTool]:
    normalize_tools_mode(mode)
    return build_tools(ctx)
