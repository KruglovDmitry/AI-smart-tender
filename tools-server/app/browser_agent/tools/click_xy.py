"""Tool: click_xy."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class ClickXyInput(BaseModel):
    x: float = Field(description="Viewport X in pixels")
    y: float = Field(description="Viewport Y in pixels")
    expect_download: bool = Field(
        default=False,
        description="True if click should trigger a file download",
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def click_xy(x: float, y: float, expect_download: bool = False) -> str:
        result = await browser_tools.click_xy(ctx.rt, x, y, expect_download)
        trace(ctx, "click_xy", {"x": x, "y": y, "expect_download": expect_download}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=click_xy,
        name="click_xy",
        description="Click at viewport coordinates (pixels).",
        args_schema=ClickXyInput,
    )
