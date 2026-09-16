"""Tool: scroll."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


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
            "КОГДА: следующие карточки в выдаче ниже fold; доскроллить до блока документов/кнопок.\n"
            "АЛЬТЕРНАТИВА: eval_js (element.scrollIntoView) для конкретного элемента; "
            "navigate на URL следующей страницы пагинации, если видна.\n"
            "После scroll почти всегда нужен свежий screenshot или get_page_text.\n"
            "ВЕРНЁТ JSON: ok, message, url."
        ),
        args_schema=ScrollInput,
    )
