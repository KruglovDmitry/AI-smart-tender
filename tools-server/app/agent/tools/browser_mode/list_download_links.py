"""Tool: list_download_links — slim link list for the LLM."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ....core.browser import primitives as browser_tools
from ...context import PlatformAgentContext, to_json, trace


class ListDownloadLinksInput(BaseModel):
    limit: int = Field(default=40, description="Максимум кандидатов в ответе")


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def list_download_links(limit: int = 40) -> str:
        result = await browser_tools.list_download_links(ctx.rt, limit)
        slim_links = []
        for item in result.get("links") or []:
            if not isinstance(item, dict):
                continue
            if item.get("kind") == "noise" or (item.get("score") or 0) <= 0:
                continue
            slim_links.append(
                {
                    "text": (item.get("text") or "")[:120],
                    "href": item.get("href"),
                    "score": item.get("score"),
                    "kind": item.get("kind"),
                }
            )
        slim = {
            "ok": result.get("ok"),
            "action": "list_download_links",
            "message": result.get("message"),
            "url": result.get("url"),
            "links": slim_links,
        }
        trace(ctx, "list_download_links", {"limit": limit}, result)
        return to_json(slim)

    return StructuredTool.from_function(
        coroutine=list_download_links,
        name="list_download_links",
        description=(
            "Сканировать DOM на кандидаты загрузок (служебный мусор отфильтрован/понижен).\n"
            "КОГДА: на странице/разделе документов перед download_url.\n"
            "Сам решай по text/href/score/kind; качай осмысленные файлы закупки.\n"
            "ВЕРНЁТ JSON: ok, url, links[{text,href,score,kind}]."
        ),
        args_schema=ListDownloadLinksInput,
    )
