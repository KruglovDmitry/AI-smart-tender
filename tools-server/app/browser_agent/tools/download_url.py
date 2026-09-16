"""Tool: download_url."""

from __future__ import annotations

from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class DownloadUrlInput(BaseModel):
    url: str = Field(description="Direct file URL to download")
    suggested_name: Optional[str] = Field(
        default=None,
        description="Filename from page link text (prefer Cyrillic names)",
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def download_url(url: str, suggested_name: str | None = None) -> str:
        result = await browser_tools.download_url(ctx.rt, url, suggested_name)
        trace(
            ctx,
            "download_url",
            {"url": url, "suggested_name": suggested_name},
            result,
        )
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=download_url,
        name="download_url",
        description="Download file URL into tenders folder.",
        args_schema=DownloadUrlInput,
    )
