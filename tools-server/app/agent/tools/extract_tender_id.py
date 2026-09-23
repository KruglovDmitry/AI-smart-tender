"""Tool: extract_tender_id."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..context import PlatformAgentContext, to_json, trace
from ...domain.tender_id import resolve_tender_id


class TenderUrlInput(BaseModel):
    url: str = Field(description="URL карточки/извещения (из выдачи или текущей страницы)")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def extract_tender_id(url: str) -> str:
        info = resolve_tender_id(url, platform=ctx.platform)
        info["platform"] = ctx.platform
        info["url"] = url
        tender_id = str(info.get("tender_id") or "").strip()
        if tender_id:
            # Только запоминаем кандидата — папку НЕ создаём (иначе куча пустых dirs).
            ctx.current_tender_id = tender_id
            ctx.current_tender_url = url
            ctx.current_tender_dir = None
            info["message"] = (
                f"tender_id={tender_id}. Дальше check_tender_seen; "
                "папка tenders/<platform>/<tender_id>/ создастся только при "
                "save_tender_overview / download_url для НОВОГО тендера."
            )
        trace(ctx, "extract_tender_id", {"url": url}, info)
        return to_json(info)

    return StructuredTool.from_function(
        coroutine=extract_tender_id,
        name="extract_tender_id",
        description=(
            "Извлечь стабильный tender_id из URL (для дедупа). Папку НЕ создаёт.\n"
            "КОГДА: на выдаче, ДО navigate на карточку — вместе с check_tender_seen "
            "или лучше одним вызовом filter_unseen_tenders(urls).\n"
            "ВЕРНЁТ JSON: tender_id, method, platform, url."
        ),
        args_schema=TenderUrlInput,
    )
