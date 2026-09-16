"""Tool: press_key."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class PressKeyInput(BaseModel):
    key: str = Field(
        default="Enter",
        description="Имя клавиши Playwright: Enter, Tab, Escape, ArrowDown, ...",
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def press_key(key: str = "Enter") -> str:
        result = await browser_tools.press_key(ctx.rt, key)
        trace(ctx, "press_key", {"key": key}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=press_key,
        name="press_key",
        description=(
            "Нажать клавишу в активном контексте страницы.\n"
            "КОГДА: Enter после type_text (отправить поиск); Escape закрыть модалку; Tab между полями.\n"
            "АЛЬТЕРНАТИВА: click_xy по кнопке Submit, если Enter не срабатывает.\n"
            "НЕ для: навигации по URL (→ navigate) и скачивания (→ download_url).\n"
            "ВЕРНЁТ JSON: ok, action, message, url."
        ),
        args_schema=PressKeyInput,
    )
