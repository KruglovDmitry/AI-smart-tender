"""Tool: download_url."""

from __future__ import annotations

from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, ensure_tender_workspace, to_json, trace


class DownloadUrlInput(BaseModel):
    url: str = Field(description="Прямой URL файла (из list_download_links kind=file)")
    suggested_name: Optional[str] = Field(
        default=None,
        description="Желаемое имя файла с страницы (лучше с расширением и кириллицей)",
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def download_url(url: str, suggested_name: str | None = None) -> str:
        if not ctx.current_tender_id:
            result = {
                "ok": False,
                "action": "download_url",
                "message": (
                    "Нет активного тендера: сначала extract_tender_id(url), "
                    "чтобы создать папку session/<tender_id>/"
                ),
            }
            trace(ctx, "download_url", {"url": url, "suggested_name": suggested_name}, result)
            return to_json(result)

        folder = ensure_tender_workspace(
            ctx, ctx.current_tender_id, ctx.current_tender_url
        )
        result = await browser_tools.download_url(ctx.rt, url, suggested_name)
        if isinstance(result, dict):
            result = {**result, "tender_id": ctx.current_tender_id, "tender_dir": str(folder)}
        trace(
            ctx,
            "download_url",
            {"url": url, "suggested_name": suggested_name},
            result,
        )
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=download_url,
        name="download_url",
        description=(
            "Скачать ФАЙЛ по прямому URL в папку ТЕКУЩЕГО тендера "
            "(session/<tender_id>/), не в общую кучу.\n"
            "КОГДА: после extract_tender_id + save_tender_overview + list_download_links "
            "(kind=file).\n"
            "АЛЬТЕРНАТИВА: click_xy(expect_download=true) для кнопки без URL "
            "(тоже в папку текущего тендера).\n"
            "НЕ передавать: HTML-страницы карточек, вкладки, футер, javascript:.\n"
            "ВЕРНЁТ JSON: ok, message, file, tender_dir, bytes."
        ),
        args_schema=DownloadUrlInput,
    )
