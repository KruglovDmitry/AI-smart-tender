"""Tool: list_download_links."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class ListDownloadLinksInput(BaseModel):
    limit: int = Field(default=40, description="Максимум кандидатов в ответе")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def list_download_links(limit: int = 40) -> str:
        result = await browser_tools.list_download_links(ctx.rt, limit)
        trace(ctx, "list_download_links", {"limit": limit}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=list_download_links,
        name="list_download_links",
        description=(
            "Сканировать DOM на кандидаты загрузок/вкладок документов.\n"
            "КОГДА: на карточке или вкладке документов перед скачиванием; понять, есть ли файлы.\n"
            "kind в элементах: file — прямой файл для download_url; tab — вкладка/раздел "
            "(сначала click_xy или navigate, не download_url); прочее — обычно пропускать.\n"
            "АЛЬТЕРНАТИВА: screenshot+VL download_hints, если DOM пустой; "
            "click_xy(expect_download=true) для кнопки без URL.\n"
            "НЕ качать футер/статистику/служебные ссылки даже если попали в список.\n"
            "ВЕРНЁТ JSON: ok, url, links[] (text, href/url, kind, ...), message."
        ),
        args_schema=ListDownloadLinksInput,
    )
