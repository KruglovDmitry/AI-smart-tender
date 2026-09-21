"""Tool: extract_tender_id."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._common import PlatformAgentContext, ensure_tender_workspace, to_json, trace
from .tender_id import resolve_tender_id


class TenderUrlInput(BaseModel):
    url: str = Field(description="URL карточки/извещения (из выдачи или текущей страницы)")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def extract_tender_id(url: str) -> str:
        info = resolve_tender_id(url, platform=ctx.platform)
        info["platform"] = ctx.platform
        info["url"] = url
        tender_id = str(info.get("tender_id") or "").strip()
        if tender_id:
            folder = ensure_tender_workspace(ctx, tender_id, url)
            info["tender_dir"] = str(folder)
            info["message"] = (
                f"tender_id={tender_id}; downloads go to {folder.name}/ "
                "(call save_tender_overview on card page before download_url)"
            )
        trace(ctx, "extract_tender_id", {"url": url}, info)
        return to_json(info)

    return StructuredTool.from_function(
        coroutine=extract_tender_id,
        name="extract_tender_id",
        description=(
            "Извлечь стабильный tender_id из URL карточки (для дедупа) и открыть "
            "папку тендера session/<tender_id>/ — все download_url пойдут туда.\n"
            "КОГДА: сразу перед check_tender_seen / mark_tender_seen, на КАЖДОМ кандидате.\n"
            "АЛЬТЕРНАТИВА: нет — не угадывай id из текста вручную; всегда этот tool.\n"
            "ВЕРНЁТ JSON: tender_id, method, platform, url, tender_dir."
        ),
        args_schema=TenderUrlInput,
    )
