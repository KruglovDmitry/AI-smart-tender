"""Tool: mark_tender_seen (SQLite dedup)."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._common import PlatformAgentContext, to_json, trace


class TenderSeenInput(BaseModel):
    tender_id: str = Field(description="tender_id строго из ответа extract_tender_id")
    tender_url: str = Field(description="URL обработанной карточки")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def mark_tender_seen(tender_id: str, tender_url: str) -> str:
        result = ctx.store.mark_seen(ctx.platform, tender_id, tender_url)
        if ctx.current_tender_dir:
            result = {**result, "tender_dir": str(ctx.current_tender_dir)}
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
        description=(
            "Пометить тендер как обработанный в SQLite (дедуп на следующие запуски).\n"
            "КОГДА: ПОСЛЕ успешной обработки кандидата (карточка открыта и/или файлы скачаны "
            "по условиям задачи). Увеличивает счётчик новых, если is_new.\n"
            "АЛЬТЕРНАТИВА: check_tender_seen — только чтение, без записи.\n"
            "НЕ вызывать до проверки is_seen и не на «пустых» проходах без реальной работы.\n"
            "ВЕРНЁТ JSON: platform, tender_id, tender_url, is_new, first_seen_at, last_seen_at."
        ),
        args_schema=TenderSeenInput,
    )
