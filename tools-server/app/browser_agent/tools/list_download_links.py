"""Tool: list_download_links."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class ListDownloadLinksInput(BaseModel):
    limit: int = Field(default=40, description="Max candidate links to return")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def list_download_links(limit: int = 40) -> str:
        result = await browser_tools.list_download_links(ctx.rt, limit)
        trace(ctx, "list_download_links", {"limit": limit}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=list_download_links,
        name="list_download_links",
        description="Scan DOM for document/download links.",
        args_schema=ListDownloadLinksInput,
    )
