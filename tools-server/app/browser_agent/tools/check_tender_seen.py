"""Tool: check_tender_seen (SQLite dedup)."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._common import PlatformAgentContext, to_json, trace


class TenderSeenInput(BaseModel):
    tender_id: str = Field(description="Stable tender id from extract_tender_id")
    tender_url: str = Field(description="Tender card URL")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def check_tender_seen(tender_id: str, tender_url: str) -> str:
        result = ctx.store.check_tender(ctx.platform, tender_id, tender_url)
        trace(
            ctx,
            "check_tender_seen",
            {"tender_id": tender_id, "tender_url": tender_url},
            result,
        )
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=check_tender_seen,
        name="check_tender_seen",
        description="Check if tender was seen before (SQLite dedup).",
        args_schema=TenderSeenInput,
    )
