"""Tool: mark_tender_seen (SQLite dedup)."""

from __future__ import annotations

from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._common import (
    PlatformAgentContext,
    ensure_tender_workspace,
    pop_pending_new,
    to_json,
    trace,
)


class TenderSeenInput(BaseModel):
    tender_id: str = Field(description="tender_id из filter_unseen / extract_tender_id")
    tender_url: str = Field(description="Exact URL карточки (из new[].tender_url)")
    count_toward_limit: Optional[bool] = Field(
        default=True,
        description=(
            "true (по умолчанию) — засчитывает в max_new_tenders. "
            "false — только дедуп (устаревший/нерелевантный кандидат после overview)."
        ),
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def mark_tender_seen(
        tender_id: str,
        tender_url: str,
        count_toward_limit: bool = True,
    ) -> str:
        folder = ensure_tender_workspace(ctx, tender_id, tender_url)
        result = ctx.store.mark_seen(ctx.platform, tender_id, tender_url)
        result = {
            **result,
            "tender_dir": str(folder),
            "counted_toward_limit": bool(count_toward_limit),
        }
        if result.get("is_new") and count_toward_limit:
            ctx.new_tenders_processed += 1
            ctx.processed_tenders.append(result)
        elif result.get("is_new") and not count_toward_limit:
            result["message"] = (
                (result.get("message") or "")
                + " Помечен seen без зачёта в лимит (stale/skip)."
            ).strip()
        pop_pending_new(ctx, tender_id)
        trace(
            ctx,
            "mark_tender_seen",
            {
                "tender_id": tender_id,
                "tender_url": tender_url,
                "count_toward_limit": count_toward_limit,
            },
            result,
        )
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=mark_tender_seen,
        name="mark_tender_seen",
        description=(
            "Пометить тендер как seen в SQLite.\n"
            "КОГДА: после обработки ИЛИ после skip устаревшего (count_toward_limit=false).\n"
            "ВЕРНЁТ JSON: is_new, counted_toward_limit, tender_dir, …"
        ),
        args_schema=TenderSeenInput,
    )
