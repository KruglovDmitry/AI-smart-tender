"""Tool: get_page_text."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class GetPageTextInput(BaseModel):
    max_chars: int = Field(default=12000, description="Max characters of visible text")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def get_page_text(max_chars: int = 12000) -> str:
        result = await browser_tools.get_page_text(ctx.rt, max_chars)
        trace(ctx, "get_page_text", {"max_chars": max_chars}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=get_page_text,
        name="get_page_text",
        description="Read visible DOM text from the page.",
        args_schema=GetPageTextInput,
    )
