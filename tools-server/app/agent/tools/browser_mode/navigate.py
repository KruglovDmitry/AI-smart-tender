"""Tool: navigate."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ....core.browser import primitives as browser_tools
from ...context import PlatformAgentContext, note_results_url, to_json, trace
from ....domain.tender_id import resolve_tender_id


class NavigateInput(BaseModel):
    url: str = Field(
        description=(
            "Полный http(s) URL. Копируй exact href из new[]/collect_card_urls/"
            "results_url — не собирай путь вручную."
        )
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def navigate(url: str) -> str:
        result = await browser_tools.navigate(ctx.rt, url)
        final_url = str((result or {}).get("url") or url or "")
        low = final_url.lower()
        if any(x in low for x in ("result", "search", "pagenumber=", "page=")):
            note_results_url(ctx, final_url)
            if "pagenumber=" in low or "page=" in low:
                ctx.platform_notes["pagination_hint"] = (
                    "URL query page / pageNumber works on this platform"
                )
        if result.get("ok"):
            info = resolve_tender_id(final_url, platform=ctx.platform)
            tid = str(info.get("tender_id") or "").strip()
            if tid and tid != (ctx.current_tender_id or ""):
                ctx.current_tender_id = tid
                ctx.current_tender_url = final_url
                ctx.current_tender_dir = None
            if tid:
                result = {**result, "tender_id": tid}
        elif isinstance(result, dict) and result.get("not_found"):
            ru = (ctx.platform_notes or {}).get("results_url")
            if ru:
                result = {
                    **result,
                    "hint": f"Вернись navigate({ru!r}) и возьми exact href из pending_new.",
                }
        trace(ctx, "navigate", {"url": url}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=navigate,
        name="navigate",
        description=(
            "Открыть exact http(s) URL.\n"
            "КОГДА: platform_url; карточка из new[].tender_url; docs href со страницы; "
            "возврат на results_url из NOTES.\n"
            "ЗАПРЕЩЕНО: собирать URL из tender_id + шаблонов (view.html/ea44/…). "
            "При ok=false not_found — не угадывай другой шаблон.\n"
            "ВЕРНЁТ JSON: ok, url, title, page_kind "
            "(not_found|search|card|documents|login|captcha|home|unknown); "
            "при 404 — ok=false, not_found=true."
        ),
        args_schema=NavigateInput,
    )
