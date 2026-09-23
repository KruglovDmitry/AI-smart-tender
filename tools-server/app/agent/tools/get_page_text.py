"""Tool: get_page_text."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...core.browser import primitives as browser_tools
from ..context import PlatformAgentContext, to_json, trace


class GetPageTextInput(BaseModel):
    max_chars: int = Field(
        default=12000,
        description="Максимум символов видимого текста (хвост обрежется)",
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def get_page_text(max_chars: int = 12000) -> str:
        result = await browser_tools.get_page_text(ctx.rt, max_chars)
        trace(ctx, "get_page_text", {"max_chars": max_chars}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=get_page_text,
        name="get_page_text",
        description=(
            "Прочитать видимый текст страницы (innerText), без картинок и координат.\n"
            "КОГДА: проверить, что площадка загрузилась; найти номера/названия в выдаче; "
            "убедиться что это карточка, а не 404; дешёвая проверка после navigate.\n"
            "АЛЬТЕРНАТИВА: screenshot — когда нужны координаты; "
            "collect_card_urls / inspect_page_nav — структура ссылок и пагинации.\n"
            "ВЕРНЁТ JSON: ok, url, title, text (обрезанный), иногда truncated."
        ),
        args_schema=GetPageTextInput,
    )
