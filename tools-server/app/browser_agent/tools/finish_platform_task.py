"""Tool: finish_platform_task."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._common import PlatformAgentContext, to_json, trace


class FinishInput(BaseModel):
    summary: str = Field(
        description=(
            "Краткий итог ТОЛЬКО из реальных tool results: URL, tender_id, файлы / причина неудачи. "
            "Без плейсхолдеров вроде result['...'] или ${...}."
        )
    )
    success: bool = Field(
        default=True,
        description="true только если критерии задачи подтверждены tool results",
    )


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
        description=(
            "Завершить задачу мониторинга. Единственный допустимый способ финала "
            "(не пиши «готово» текстом без вызова).\n"
            "КОГДА: лимит новых обработан ИЛИ кандидаты исчерпаны ИЛИ блокер (login/captcha/403). "
            "Вызывай ОДИН раз.\n"
            "success=true — только при подтверждённых критериях (URL выдачи/карточки, файлы, "
            "processed_tenders). Иначе success=false с причиной.\n"
            "АЛЬТЕРНАТИВА: нет. Не заменяет mark_tender_seen / download_url.\n"
            "ВЕРНЁТ JSON: success, message, new_tenders_processed, processed_tenders, "
            "downloaded_files, url, done=true. return_direct — цикл агента останавливается."
        ),
        args_schema=FinishInput,
        return_direct=True,
    )
