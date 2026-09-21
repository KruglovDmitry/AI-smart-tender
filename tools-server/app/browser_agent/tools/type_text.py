"""Tool: type_text."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ... import config
from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


def _clamp_xy(ctx: PlatformAgentContext, x: float, y: float) -> tuple[float, float]:
    vp = getattr(ctx.rt.page, "viewport_size", None) or {}
    w = float(vp.get("width") or config.BROWSER_VIEWPORT_WIDTH)
    h = float(vp.get("height") or config.BROWSER_VIEWPORT_HEIGHT)
    return max(0.0, min(float(x), w - 1.0)), max(0.0, min(float(y), h - 1.0))


class TypeTextInput(BaseModel):
    text: str = Field(description="Текст для ввода (например keywords)")
    x: float = Field(description="Обязательный viewport X — клик по полю перед вводом")
    y: float = Field(description="Обязательный viewport Y — клик по полю перед вводом")
    clear: bool = Field(default=True, description="Очистить поле перед вводом (обычно true)")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def type_text(
        text: str,
        x: float,
        y: float,
        clear: bool = True,
    ) -> str:
        cx, cy = _clamp_xy(ctx, x, y)
        result = await browser_tools.type_text(ctx.rt, text, cx, cy, clear)
        if cx != float(x) or cy != float(y):
            result = {
                **result,
                "clamped": True,
                "x": cx,
                "y": cy,
                "requested": {"x": x, "y": y},
            }
        trace(ctx, "type_text", {"text": text, "x": cx, "y": cy, "clear": clear}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=type_text,
        name="type_text",
        description=(
            "Клик по полю (x,y) и ввод текста с клавиатуры.\n"
            "КОГДА: заполнить поиск/фильтр на UI площадки. После ввода обычно press_key Enter "
            "или click_xy по кнопке поиска; затем screenshot для проверки выдачи.\n"
            "АЛЬТЕРНАТИВА: если поле не находится — новый screenshot и другие x,y; "
            "inspect_page_nav подскажет, есть ли search_inputs на странице.\n"
            "ЗАПРЕЩЕНО: вызывать без x,y. Координаты — только из свежего screenshot.\n"
            "ВЕРНЁТ JSON: ok, message, url; при clamp — clamped, x, y, requested."
        ),
        args_schema=TypeTextInput,
    )
