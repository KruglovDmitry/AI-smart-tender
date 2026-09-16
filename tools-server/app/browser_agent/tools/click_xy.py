"""Tool: click_xy."""

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


class ClickXyInput(BaseModel):
    x: float = Field(description="Viewport X в пикселях из свежего screenshot/VL")
    y: float = Field(description="Viewport Y в пикселях из свежего screenshot/VL")
    expect_download: bool = Field(
        default=False,
        description="True, если клик должен инициировать скачивание файла (без прямого URL)",
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def click_xy(x: float, y: float, expect_download: bool = False) -> str:
        cx, cy = _clamp_xy(ctx, x, y)
        result = await browser_tools.click_xy(ctx.rt, cx, cy, expect_download)
        if cx != float(x) or cy != float(y):
            result = {**result, "clamped": True, "x": cx, "y": cy, "requested": {"x": x, "y": y}}
        trace(ctx, "click_xy", {"x": cx, "y": cy, "expect_download": expect_download}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=click_xy,
        name="click_xy",
        description=(
            "Клик мышью по координатам viewport (пиксели).\n"
            "КОГДА: кнопка «Найти», вкладка «Документы», ссылка без удобного href, "
            "скачивание кнопкой без URL (expect_download=true).\n"
            "АЛЬТЕРНАТИВА: navigate — если есть прямой URL карточки/страницы; "
            "download_url — если list_download_links дал kind=file с url; "
            "eval_js — программный click по селектору, если координаты нестабильны.\n"
            "ОБЯЗАТЕЛЬНО: x,y только из последнего screenshot (иначе промах). "
            "Выход за viewport будет clamped.\n"
            "ВЕРНЁТ JSON: ok, message, url; при expect_download — сведения о файле/ошибке; "
            "иногда clamped + requested."
        ),
        args_schema=ClickXyInput,
    )
