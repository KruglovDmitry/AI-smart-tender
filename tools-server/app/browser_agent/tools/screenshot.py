"""Tool: screenshot (+ VL analysis)."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from ... import config
from ...browser_tool import tools as browser_tools
from ...browser_tool.vision import analyze_screenshot, format_vl_for_agent
from ._common import PlatformAgentContext, to_json, trace


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def screenshot() -> str:
        """Capture viewport screenshot; response includes VL analysis text."""
        result = await browser_tools.screenshot(ctx.rt)
        extra = ""
        if (
            result.get("ok")
            and config.AGENT_VL_ENABLED
            and ctx.rt.last_screenshot_b64
        ):
            vl = await analyze_screenshot(
                image_b64=ctx.rt.last_screenshot_b64,
                width=int(result.get("width") or config.BROWSER_VIEWPORT_WIDTH),
                height=int(result.get("height") or config.BROWSER_VIEWPORT_HEIGHT),
                url=str(result.get("url") or ctx.rt.page.url),
                task=ctx.task_hint or ctx.keywords,
            )
            result["vl"] = {
                "ok": vl.get("ok"),
                "model": vl.get("model"),
                "advice": vl.get("advice"),
                "error": vl.get("error"),
            }
            trace(
                ctx,
                "vl_analyze_screenshot",
                {"model": config.AGENT_VL_MODEL},
                result["vl"],
            )
            extra = "\n\n" + format_vl_for_agent(vl)
        trace(ctx, "screenshot", {}, {k: v for k, v in result.items() if k != "vl"})
        return to_json(result) + extra

    return StructuredTool.from_function(
        coroutine=screenshot,
        name="screenshot",
        description=(
            "Capture viewport screenshot. Response includes VL analysis "
            "(page_summary, click hints). Use when DOM is unclear."
        ),
    )
