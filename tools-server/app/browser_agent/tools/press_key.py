"""Tool: press_key."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class PressKeyInput(BaseModel):
    key: str = Field(default="Enter", description="Key name, e.g. Enter, Tab, Escape")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def press_key(key: str = "Enter") -> str:
        result = await browser_tools.press_key(ctx.rt, key)
        trace(ctx, "press_key", {"key": key}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=press_key,
        name="press_key",
        description="Press a keyboard key (Enter, Tab, Escape, ...).",
        args_schema=PressKeyInput,
    )
