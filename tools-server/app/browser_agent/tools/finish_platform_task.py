"""Tool: finish_platform_task."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._common import PlatformAgentContext, to_json, trace


class FinishInput(BaseModel):
    summary: str = Field(description="Short summary of what was done")
    success: bool = Field(default=True, description="Whether the task succeeded")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def finish_platform_task(summary: str, success: bool = True) -> str:
        ctx.done = True
        ctx.final_summary = summary
        ctx.final_success = success
        result = {
            "ok": True,
            "action": "finish_platform_task",
            "success": success,
            "message": summary,
            "new_tenders_processed": ctx.new_tenders_processed,
            "max_new_tenders": ctx.max_new_tenders,
            "processed_tenders": ctx.processed_tenders,
            "downloaded_files": list(ctx.rt.downloaded_files),
            "url": ctx.rt.page.url,
            "done": True,
        }
        trace(ctx, "finish_platform_task", {"summary": summary, "success": success}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=finish_platform_task,
        name="finish_platform_task",
        description="Finish the platform monitoring task with summary.",
        args_schema=FinishInput,
    )
