"""Tool: wait."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class WaitInput(BaseModel):
    seconds: float = Field(
        default=1.0,
        description="Пауза в секундах (обычно 0.5–3; не злоупотреблять лимитом шагов)",
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def wait(seconds: float = 1.0) -> str:
        result = await browser_tools.wait(ctx.rt, seconds)
        trace(ctx, "wait", {"seconds": seconds}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=wait,
        name="wait",
        description=(
            "Пауза N секунд (догрузка SPA, анимации, сеть).\n"
            "КОГДА: сразу после navigate/click/поиска, если контент ещё пустой; перед screenshot.\n"
            "АЛЬТЕРНАТИВА: повторный get_page_text/screenshot без длинного wait, если страница уже готова.\n"
            "НЕ использовать как основной способ «проверить успех» — после wait нужна верификация.\n"
            "ВЕРНЁТ JSON: ok, message, url."
        ),
        args_schema=WaitInput,
    )
