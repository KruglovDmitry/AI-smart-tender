"""Tool: wait."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class WaitInput(BaseModel):
    seconds: float = Field(default=1.0, description="Seconds to wait")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def wait(seconds: float = 1.0) -> str:
        result = await browser_tools.wait(ctx.rt, seconds)
        trace(ctx, "wait", {"seconds": seconds}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=wait,
        name="wait",
        description="Wait N seconds for page load.",
        args_schema=WaitInput,
    )
