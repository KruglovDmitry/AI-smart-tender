"""Tool: download_url."""

from __future__ import annotations

from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class DownloadUrlInput(BaseModel):
    url: str = Field(description="Прямой URL файла (из list_download_links kind=file)")
    suggested_name: Optional[str] = Field(
        default=None,
        description="Желаемое имя файла с страницы (лучше с расширением и кириллицей)",
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def download_url(url: str, suggested_name: str | None = None) -> str:
        result = await browser_tools.download_url(ctx.rt, url, suggested_name)
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
            "Скачать ФАЙЛ по прямому URL в каталог загрузок сессии.\n"
            "КОГДА: после list_download_links взять 1–3 пункта kind=file (документация, архивы).\n"
            "АЛЬТЕРНАТИВА: click_xy(expect_download=true), если файла нет в links, только кнопка.\n"
            "НЕ передавать: HTML-страницы карточек, вкладки, футер, javascript:.\n"
            "ВЕРНЁТ JSON: ok, message, path/имя файла, размер; ok=false если отклонено "
            "(HTML-заглушка и т.п.). Успех подтверждай ok и наличием файла, не догадками."
        ),
        args_schema=DownloadUrlInput,
    )
