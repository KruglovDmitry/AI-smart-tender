"""Agent tools — platform (default) and browser ablation."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from ...domain.dedup import SeenTenderStore
from ...domain.tender_id import platform_from_url, resolve_tender_id
from ..context import PlatformAgentContext, make_context
from .high_level import PLATFORM_TOOL_NAMES, build_high_level_tools

__all__ = [
    "PlatformAgentContext",
    "SeenTenderStore",
    "AGENT_TOOL_MODES",
    "PLATFORM_TOOL_NAMES",
    "build_langchain_tools",
    "make_context",
    "normalize_tools_mode",
    "platform_from_url",
    "resolve_tender_id",
]

# platform — production; browser — ablation only. "full" is rejected.
AGENT_TOOL_MODES = ("platform", "browser")


def normalize_tools_mode(mode: str | None) -> str:
    m = (mode or "platform").strip().lower()
    if m == "full":
        raise ValueError(
            "tools_mode='full' удалён. Используй 'platform' (по умолчанию) "
            "или 'browser' (только для абляций/сравнения)."
        )
    if m in {"primitive", "low", "browser_only", "raw"}:
        return "browser"
    if m in {"adapter", "high", "hl", ""}:
        return "platform"
    if m not in AGENT_TOOL_MODES:
        raise ValueError(
            f"Unknown tools_mode={mode!r}. Allowed: platform | browser"
        )
    return m


def build_langchain_tools(
    ctx: PlatformAgentContext,
    mode: str | None = None,
) -> list[StructuredTool]:
    mode = normalize_tools_mode(mode)
    if mode == "browser":
        from .browser_mode import build_browser_tools

        return build_browser_tools(ctx)
    return build_high_level_tools(ctx)
