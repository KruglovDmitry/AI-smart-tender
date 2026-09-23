"""Tool: finish_platform_task."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..context import PlatformAgentContext, to_json, trace


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


def _is_search_only(task_hint: str) -> bool:
    hint_l = (task_hint or "").lower()
    return any(
        x in hint_l
        for x in ("сценарий 2", "не открывай карточ", "не открывай карточки", "только поиск")
    )


def resolve_finish_success(
    requested_success: bool,
    *,
    processed_tenders: list[Any] | None,
    downloaded_files: list[Any] | None,
    task_hint: str = "",
    summary: str = "",
) -> tuple[bool, str]:
    """
    Structural success gate: success=True only with facts or search_only hint.
    Never upgrades success=False to True. Does not parse summary prose for claims.
    """
    summary = summary or ""
    if not requested_success:
        return False, summary

    has_facts = bool(processed_tenders) or bool(downloaded_files)
    if has_facts:
        return True, summary
    if _is_search_only(task_hint):
        return True, summary

    rejected = (
        "ОТКЛОНЕНО авто-проверкой: success=true без processed_tenders и без downloaded_files. "
        f"Было: {summary}"
    )
    return False, rejected


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def finish_platform_task(summary: str, success: bool = True) -> str:
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
            "action": "finish_platform_task",
            "success": success,
            "message": summary,
            "new_tenders_processed": ctx.new_tenders_processed,
            "max_new_tenders": ctx.max_new_tenders,
            "processed_tenders": ctx.processed_tenders,
            "downloaded_files": list(ctx.rt.downloaded_files),
            "url": current_url,
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
            "success=true — только при подтверждённых фактах: processed_tenders и/или "
            "downloaded_files (или явный search-only сценарий). Иначе success=false.\n"
            "АЛЬТЕРНАТИВА: нет. Не заменяет mark_tender_seen / download_url.\n"
            "ВЕРНЁТ JSON: success, message, new_tenders_processed, processed_tenders, "
            "downloaded_files, url, done=true. return_direct — цикл агента останавливается."
        ),
        args_schema=FinishInput,
        return_direct=True,
    )
