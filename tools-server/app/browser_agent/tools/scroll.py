"""Tool: scroll."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class ScrollInput(BaseModel):
    delta_y: int = Field(default=600, description="Vertical scroll delta in pixels")
    times: int = Field(default=1, description="How many times to scroll")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def scroll(delta_y: int = 600, times: int = 1) -> str:
        result = await browser_tools.scroll(ctx.rt, delta_y, times)
        trace(ctx, "scroll", {"delta_y": delta_y, "times": times}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=scroll,
        name="scroll",
        description="Scroll the page vertically.",
        args_schema=ScrollInput,
    )
