"""Tool: mark_tender_seen (SQLite dedup)."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._common import PlatformAgentContext, to_json, trace


class TenderSeenInput(BaseModel):
    tender_id: str = Field(description="Stable tender id from extract_tender_id")
    tender_url: str = Field(description="Tender card URL")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def mark_tender_seen(tender_id: str, tender_url: str) -> str:
        result = ctx.store.mark_seen(ctx.platform, tender_id, tender_url)
        if result.get("is_new"):
            ctx.new_tenders_processed += 1
            ctx.processed_tenders.append(result)
        trace(
            ctx,
            "mark_tender_seen",
            {"tender_id": tender_id, "tender_url": tender_url},
            result,
        )
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=mark_tender_seen,
        name="mark_tender_seen",
        description="Mark tender as seen after processing.",
        args_schema=TenderSeenInput,
    )
