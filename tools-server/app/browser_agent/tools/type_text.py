"""Tool: type_text."""

from __future__ import annotations

from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class TypeTextInput(BaseModel):
    text: str = Field(description="Text to type")
    x: Optional[float] = Field(default=None, description="Optional click X before typing")
    y: Optional[float] = Field(default=None, description="Optional click Y before typing")
    clear: bool = Field(default=True, description="Clear field before typing")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def type_text(
        text: str,
        x: float | None = None,
        y: float | None = None,
        clear: bool = True,
    ) -> str:
        result = await browser_tools.type_text(ctx.rt, text, x, y, clear)
        trace(ctx, "type_text", {"text": text, "x": x, "y": y, "clear": clear}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=type_text,
        name="type_text",
        description="Type text; optionally click (x,y) first.",
        args_schema=TypeTextInput,
    )
