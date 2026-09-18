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
        current_url = ctx.rt.page.url or ""
        url_l = current_url.lower()
        sum_l = (summary or "").lower()
        hint_l = (ctx.task_hint or "").lower()
        on_listing = "results.html" in url_l or "extendedsearch" in url_l
        # сценарий «только поиск» — finish на выдаче нормален
        search_only = any(
            x in hint_l
            for x in ("сценарий 2", "не открывай карточ", "не открывай карточки", "только поиск")
        )
        denies_opening = any(
            x in sum_l
            for x in (
                "не открыв",
                "не открывал",
                "карточки не",
                "карточку не",
                "без открыт",
                "не качал",
                "не скачив",
            )
        )
        # ловим только явную претензию, что карточка уже открыта/обработана
        claims_opened_card = (not denies_opening) and any(
            x in sum_l
            for x in (
                "notice/",
                "regnumber=",
                "common-info",
                "открыта карточ",
                "открыл карточ",
                "открыта первая",
                "обработана карточ",
                "обработан тендер",
            )
        )
        if (
            success
            and on_listing
            and claims_opened_card
            and not search_only
            and "notice" not in url_l
        ):
            success = False
            summary = (
                f"ОТКЛОНЕНО авто-проверкой: success=true при URL выдачи ({current_url}). "
                f"Сначала navigate на карточку и подтверди смену URL. Было: {summary}"
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
            "success=true — только при подтверждённых критериях (URL выдачи/карточки, файлы, "
            "processed_tenders). Иначе success=false с причиной. "
            "Нельзя success=true про карточку, если url в ответе всё ещё выдача/поиск.\n"
            "АЛЬТЕРНАТИВА: нет. Не заменяет mark_tender_seen / download_url.\n"
            "ВЕРНЁТ JSON: success, message, new_tenders_processed, processed_tenders, "
            "downloaded_files, url, done=true. return_direct — цикл агента останавливается."
        ),
        args_schema=FinishInput,
        return_direct=True,
    )
