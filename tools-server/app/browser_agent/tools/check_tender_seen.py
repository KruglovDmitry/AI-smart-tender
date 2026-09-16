"""Tool: check_tender_seen (SQLite dedup)."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._common import PlatformAgentContext, to_json, trace


class TenderSeenInput(BaseModel):
    tender_id: str = Field(description="tender_id строго из ответа extract_tender_id")
    tender_url: str = Field(description="URL той же карточки")


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
        description=(
            "Проверить в SQLite, видели ли уже этот tender_id на площадке (без записи).\n"
            "КОГДА: после extract_tender_id, ДО открытия/скачивания. Если is_seen=true — пропусти кандидата.\n"
            "АЛЬТЕРНАТИВА: mark_tender_seen пишет в базу — вызывай его только после реальной обработки.\n"
            "ВЕРНЁТ JSON: platform, tender_id, tender_url, is_seen, is_new (=не is_seen)."
        ),
        args_schema=TenderSeenInput,
    )
