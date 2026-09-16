"""Tool: navigate."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class NavigateInput(BaseModel):
    url: str = Field(description="http(s) URL to open")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def navigate(url: str) -> str:
        result = await browser_tools.navigate(ctx.rt, url)
        trace(ctx, "navigate", {"url": url}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=navigate,
        name="navigate",
        description="Open a URL in the browser.",
        args_schema=NavigateInput,
    )
