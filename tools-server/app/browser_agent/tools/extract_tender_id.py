"""Tool: extract_tender_id."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._common import PlatformAgentContext, to_json, trace
from .tender_id import resolve_tender_id


class TenderUrlInput(BaseModel):
    url: str = Field(description="Tender card URL")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def extract_tender_id(url: str) -> str:
        info = resolve_tender_id(url, platform=ctx.platform)
        info["platform"] = ctx.platform
        info["url"] = url
        trace(ctx, "extract_tender_id", {"url": url}, info)
        return to_json(info)

    return StructuredTool.from_function(
        coroutine=extract_tender_id,
        name="extract_tender_id",
        description="Extract stable tender_id from a tender card URL.",
        args_schema=TenderUrlInput,
    )
