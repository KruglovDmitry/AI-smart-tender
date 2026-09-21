"""Tool: filter_unseen_tenders — batch dedup on search results without navigate."""

from __future__ import annotations

from typing import List

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._common import PlatformAgentContext, note_pending_new, note_results_url, to_json, trace
from .tender_id import resolve_tender_id


class FilterUnseenInput(BaseModel):
    urls: List[str] = Field(
        description=(
            "Список URL карточек с ТЕКУЩЕЙ страницы выдачи (сверху вниз), "
            "лучше из collect_card_urls.urls. До 40 штук."
        )
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def filter_unseen_tenders(urls: list[str]) -> str:
        new_items: list[dict] = []
        seen_items: list[dict] = []
        seen_ids: set[str] = set()
        for raw in urls or []:
            url = (raw or "").strip()
            if not url or not url.startswith("http"):
                continue
            info = resolve_tender_id(url, platform=ctx.platform)
            tid = str(info.get("tender_id") or "").strip()
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)
            row = {
                "tender_id": tid,
                "tender_url": url,
                "method": info.get("method"),
            }
            if ctx.store.is_seen(ctx.platform, tid):
                seen_items.append(row)
            else:
                new_items.append(row)

        try:
            note_results_url(ctx, ctx.rt.page.url)
        except Exception:
            pass
        note_pending_new(ctx, new_items)

        full = {
            "ok": True,
            "action": "filter_unseen_tenders",
            "platform": ctx.platform,
            "new_count": len(new_items),
            "seen_count": len(seen_items),
            "new": new_items,
            "seen": seen_items[:30],
            "message": (
                f"new={len(new_items)}, seen={len(seen_items)}. "
                "navigate ТОЛЬКО по exact new[].tender_url (не собирай URL). "
                "Если new=0 — СМЕНА СТРАНИЦЫ выдачи (query page/pageNumber/p или кнопка next), "
                "НЕ крути scroll+collect по той же странице."
            ),
        }
        slim = {
            "ok": True,
            "action": "filter_unseen_tenders",
            "platform": ctx.platform,
            "new_count": len(new_items),
            "seen_count": len(seen_items),
            "new": new_items,
            "seen_ids_sample": [x["tender_id"] for x in seen_items[:5]],
            "results_url": (ctx.platform_notes or {}).get("results_url"),
            "message": full["message"],
        }
        trace(ctx, "filter_unseen_tenders", {"urls_count": len(urls or [])}, full)
        return to_json(slim)

    return StructuredTool.from_function(
        coroutine=filter_unseen_tenders,
        name="filter_unseen_tenders",
        description=(
            "Пакетно отфильтровать URL с выдачи: new vs seen (SQLite), без navigate.\n"
            "КОГДА: сразу после collect_card_urls.\n"
            "Дальше: navigate(exact new[i].tender_url). new=0 → пагинация, не scroll-loop.\n"
            "ВЕРНЁТ JSON: new[], new_count, seen_count, results_url."
        ),
        args_schema=FilterUnseenInput,
    )
