"""Tool: scroll."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ....core.browser import primitives as browser_tools
from ...context import PlatformAgentContext, to_json, trace


class ScrollInput(BaseModel):
    delta_y: int = Field(
        default=600,
        description="Сдвиг по вертикали в px: >0 вниз, <0 вверх",
    )
    times: int = Field(default=1, description="Сколько раз повторить сдвиг")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def scroll(delta_y: int = 600, times: int = 1) -> str:
        result = await browser_tools.scroll(ctx.rt, delta_y, times)
        trace(ctx, "scroll", {"delta_y": delta_y, "times": times}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=scroll,
        name="scroll",
        description=(
            "Прокрутить страницу по вертикали (viewport).\n"
            "КОГДА: контент ниже fold (редко). Для пагинации — сначала inspect_page_nav, "
            "не крути scroll в цикле.\n"
            "АЛЬТЕРНАТИВА: navigate(suggested_next_url / next.href).\n"
            "После scroll НЕ делай screenshot «на автомате» — только если нужен click_xy.\n"
            "ВЕРНЁТ JSON: ok, message, url."
        ),
        args_schema=ScrollInput,
    )
