"""Browser-mode finish tool (ablation)."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ....domain.finish import resolve_finish_success
from ...context import PlatformAgentContext, to_json, trace


class FinishInput(BaseModel):
    summary: str = Field(description="Краткий итог из tool results")
    success: bool = Field(default=True)


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def finish(summary: str, success: bool = True) -> str:
        current_url = ctx.rt.page.url or ""
        success, summary = resolve_finish_success(
            success,
            processed_tenders=ctx.processed_tenders,
            downloaded_files=list(ctx.rt.downloaded_files),
            task_hint=ctx.task_hint or "",
            summary=summary,
        )
        ctx.done = True
        ctx.final_summary = summary
        ctx.final_success = success
        result = {
            "ok": True,
            "action": "finish",
            "success": success,
            "message": summary,
            "new_tenders_processed": ctx.new_tenders_processed,
            "processed_tenders": ctx.processed_tenders,
            "downloaded_files": list(ctx.rt.downloaded_files),
            "url": current_url,
            "done": True,
        }
        trace(ctx, "finish", {"summary": summary, "success": success}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=finish,
        name="finish",
        description="Завершить задачу (browser ablation mode).",
        args_schema=FinishInput,
        return_direct=True,
    )
