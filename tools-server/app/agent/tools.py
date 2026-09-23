"""High-level platform tools — delegate to adapters + domain."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..browser_agent.tools import save_tender_overview as overview_tool
from ..browser_agent.tools._common import (
    PlatformAgentContext,
    ensure_tender_workspace,
    note_pending_new,
    note_results_url,
    pop_pending_new,
    to_json,
    trace,
)
from ..browser_agent.tools.finish_platform_task import resolve_finish_success
from ..core.browser import dom as browser_dom
from ..core.browser.primitives import download_url as core_download
from ..core.browser.primitives import navigate as core_navigate
from ..core.browser.primitives import screenshot as core_screenshot
from ..core.llm import vision as vision_mod
from ..domain import manifest as manifest_mod
from ..platforms.base import CardRef, SearchSpec
from ..platforms.registry import get_adapter


class KeywordsInput(BaseModel):
    keywords: str = Field(description="Ключевые слова поиска")


class CardUrlInput(BaseModel):
    card_url: str = Field(description="Exact URL карточки из list_new_cards")


class DownloadDocInput(BaseModel):
    url: str = Field(description="Exact URL файла из list_tender_documents")
    name: str = Field(default="", description="Имя файла / текст ссылки")


class MarkProcessedInput(BaseModel):
    tender_id: str = Field(description="tender_id")
    tender_url: str = Field(default="", description="URL карточки")
    count_toward_limit: bool = Field(default=True)


class LocateInput(BaseModel):
    goal: str = Field(description="Что найти на экране (на русском)")


class DomIdInput(BaseModel):
    el_id: int = Field(description="id из dom_snapshot / query")


class FillInput(BaseModel):
    el_id: int
    text: str


class NavigateInput(BaseModel):
    url: str


class FinishInput(BaseModel):
    summary: str
    success: bool = True


def _adapter(ctx: PlatformAgentContext):
    url = ""
    try:
        url = ctx.rt.page.url or ""
    except Exception:
        url = ""
    # Prefer platform from context host if page not ready
    seed = url or f"https://{ctx.platform}/"
    return get_adapter(seed)


def build_high_level_tools(ctx: PlatformAgentContext) -> list[StructuredTool]:
    tools: list[StructuredTool] = []

    async def open_platform_search(keywords: str) -> str:
        ad = _adapter(ctx)
        spec = SearchSpec(keywords=keywords or ctx.keywords)
        step = await ad.open_search(ctx.rt, spec)
        note_results_url(ctx, step.url)
        result = {
            "ok": step.ok,
            "action": "open_platform_search",
            "page_kind": step.page_kind,
            "url": step.url,
            "note": step.note,
            "data": step.data,
            "adapter": getattr(ad, "display_name", ad.host),
        }
        trace(ctx, "open_platform_search", {"keywords": keywords}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=open_platform_search,
            name="open_platform_search",
            description="Открыть поиск на площадке (URL-шаблон или UI). ВЕРНЁТ page_kind/url.",
            args_schema=KeywordsInput,
        )
    )

    async def list_new_cards() -> str:
        ad = _adapter(ctx)
        cards = await ad.collect_cards(ctx.rt)
        new_items: list[dict[str, Any]] = []
        seen_items: list[dict[str, Any]] = []
        for c in cards:
            tid = c.tender_id or ad.tender_id(c.url) or ""
            if not tid:
                continue
            check = ctx.store.check_tender(ctx.platform, tid, c.url)
            row = {
                "tender_id": tid,
                "tender_url": c.url,
                "title": c.title,
                "law": c.law,
                "is_new": check.get("is_new"),
            }
            if check.get("is_new"):
                new_items.append(row)
            else:
                seen_items.append(row)
        note_pending_new(ctx, new_items)
        note_results_url(ctx, ctx.rt.page.url)
        result = {
            "ok": True,
            "action": "list_new_cards",
            "adapter": getattr(ad, "display_name", ad.host),
            "total": len(cards),
            "new_count": len(new_items),
            "new": new_items,
            "seen_count": len(seen_items),
            "message": (
                f"new={len(new_items)} / total_cards={len(cards)}. "
                "Дальше open_tender(new[].tender_url)."
                if new_items
                else "new=0 — goto_next_page или finish."
            ),
        }
        trace(ctx, "list_new_cards", {}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=list_new_cards,
            name="list_new_cards",
            description="Собрать карточки (adapter) и отфильтровать unseen. ВЕРНЁТ new[].",
        )
    )

    async def open_tender(card_url: str) -> str:
        ad = _adapter(ctx)
        card = CardRef(url=card_url, tender_id=ad.tender_id(card_url))
        step = await ad.open_card(ctx.rt, card)
        tid = (step.data or {}).get("tender_id") or card.tender_id
        if tid:
            ensure_tender_workspace(ctx, str(tid), step.url or card_url)
            manifest_mod.ensure_manifest(
                Path(ctx.current_tender_dir or ctx.rt.downloads_dir),
                tender_id=str(tid),
                platform=ctx.platform,
                tender_url=step.url or card_url,
            )
        result = {
            "ok": step.ok,
            "action": "open_tender",
            "page_kind": step.page_kind,
            "url": step.url,
            "note": step.note,
            "data": step.data,
            "tender_id": tid,
        }
        trace(ctx, "open_tender", {"card_url": card_url}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=open_tender,
            name="open_tender",
            description="Открыть карточку по exact URL (adapter знает 44/223 и SPA).",
            args_schema=CardUrlInput,
        )
    )

    async def list_tender_documents() -> str:
        ad = _adapter(ctx)
        docs = await ad.collect_documents(ctx.rt)
        result = {
            "ok": True,
            "action": "list_tender_documents",
            "count": len(docs),
            "documents": [
                {"url": d.url, "name": d.name, "kind": d.kind} for d in docs
            ],
            "message": (
                f"Найдено {len(docs)} документов. Дальше download_document."
                if docs
                else "Документов не найдено."
            ),
        }
        trace(ctx, "list_tender_documents", {}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=list_tender_documents,
            name="list_tender_documents",
            description="Список документов текущей карточки (детерминированно через adapter).",
        )
    )

    async def download_document(url: str, name: str = "") -> str:
        if ctx.current_tender_id:
            ensure_tender_workspace(
                ctx, ctx.current_tender_id, ctx.current_tender_url
            )
        result = await core_download(ctx.rt, url, suggested_name=name or None)
        if result.get("ok") and ctx.current_tender_dir:
            file_path = result.get("file") or ""
            manifest_mod.append_file(
                Path(ctx.current_tender_dir),
                name=Path(str(file_path)).name if file_path else (name or "document"),
                sha256=str(result.get("sha256") or ""),
                bytes_count=int(result.get("bytes") or 0),
                source_url=str(result.get("source_url") or url),
                content_type=str(result.get("content_type") or ""),
                tender_id=str(ctx.current_tender_id or ""),
                platform=ctx.platform,
                tender_url=str(ctx.current_tender_url or ""),
            )
            result["manifest"] = str(
                manifest_mod.manifest_path(Path(ctx.current_tender_dir))
            )
        trace(ctx, "download_document", {"url": url, "name": name}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=download_document,
            name="download_document",
            description="Скачать документ и дописать в manifest.json.",
            args_schema=DownloadDocInput,
        )
    )

    # Overview: keep legacy name + plan alias
    ov_tool = overview_tool.make_tool(ctx)
    tools.append(ov_tool)

    async def save_overview() -> str:
        return await ov_tool.ainvoke({})

    tools.append(
        StructuredTool.from_function(
            coroutine=save_overview,
            name="save_overview",
            description="Сохранить overview.json (+ поля в manifest). Синоним save_tender_overview.",
        )
    )

    async def mark_processed(
        tender_id: str,
        tender_url: str = "",
        count_toward_limit: bool = True,
    ) -> str:
        turl = tender_url or ctx.current_tender_url or ""
        folder = ensure_tender_workspace(ctx, tender_id, turl)
        result = ctx.store.mark_seen(ctx.platform, tender_id, turl)
        result = {
            **result,
            "tender_dir": str(folder),
            "counted_toward_limit": bool(count_toward_limit),
            "action": "mark_processed",
        }
        if result.get("is_new") and count_toward_limit:
            ctx.new_tenders_processed += 1
            ctx.processed_tenders.append(result)
        manifest_mod.ensure_manifest(
            folder,
            tender_id=tender_id,
            platform=ctx.platform,
            tender_url=turl,
        )
        pop_pending_new(ctx, tender_id)
        trace(
            ctx,
            "mark_processed",
            {
                "tender_id": tender_id,
                "tender_url": turl,
                "count_toward_limit": count_toward_limit,
            },
            result,
        )
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=mark_processed,
            name="mark_processed",
            description="Зачесть тендер (dedup + счётчик + manifest).",
            args_schema=MarkProcessedInput,
        )
    )

    async def goto_next_page() -> str:
        ad = _adapter(ctx)
        ok = await ad.next_page(ctx.rt)
        result = {
            "ok": ok,
            "action": "goto_next_page",
            "url": ctx.rt.page.url,
            "message": "Перешли на следующую страницу" if ok else "Пагинация не удалась",
        }
        if ok:
            note_results_url(ctx, ctx.rt.page.url)
        trace(ctx, "goto_next_page", {}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=goto_next_page,
            name="goto_next_page",
            description="Следующая страница выдачи (adapter / vision fallback).",
        )
    )

    async def locate_on_screen(goal: str) -> str:
        shot = await core_screenshot(ctx.rt)
        if not shot.get("ok") or not ctx.rt.last_screenshot_b64:
            result = {"ok": False, "action": "locate_on_screen", "message": "screenshot failed"}
            trace(ctx, "locate_on_screen", {"goal": goal}, result)
            return to_json(result)
        vp = (int(shot.get("width") or 1280), int(shot.get("height") or 900))
        loc = await vision_mod.locate(ctx.rt.last_screenshot_b64, goal, vp)
        result = {"ok": True, "action": "locate_on_screen", **loc, "viewport": list(vp)}
        trace(ctx, "locate_on_screen", {"goal": goal}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=locate_on_screen,
            name="locate_on_screen",
            description="VL-fallback: найти элемент на скрине по goal → {found,x,y}.",
            args_schema=LocateInput,
        )
    )

    async def finish(summary: str, success: bool = True) -> str:
        success, summary = resolve_finish_success(
            success,
            processed_tenders=ctx.processed_tenders,
            downloaded_files=list(ctx.rt.downloaded_files),
            task_hint=ctx.task_hint or "",
            summary=summary,
        )
        ctx.done = True
        ctx.final_summary = summary
        ctx.final_success = success
        result = {
            "ok": True,
            "action": "finish",
            "success": success,
            "message": summary,
            "new_tenders_processed": ctx.new_tenders_processed,
            "processed_tenders": ctx.processed_tenders,
            "downloaded_files": list(ctx.rt.downloaded_files),
            "url": ctx.rt.page.url,
            "done": True,
        }
        trace(ctx, "finish", {"summary": summary, "success": success}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=finish,
            name="finish",
            description="Завершить задачу (структурный success). Синоним finish_platform_task.",
            args_schema=FinishInput,
            return_direct=True,
        )
    )

    # Escape hatches
    async def dom_snapshot() -> str:
        result = await browser_dom.snapshot_interactive(ctx.rt)
        trace(ctx, "dom_snapshot", {}, result)
        return to_json(result)

    async def click_element(el_id: int) -> str:
        result = await browser_dom.click_by_id(ctx.rt, el_id)
        trace(ctx, "click_element", {"el_id": el_id}, result)
        return to_json(result)

    async def fill_element(el_id: int, text: str) -> str:
        result = await browser_dom.fill_by_id(ctx.rt, el_id, text)
        trace(ctx, "fill_element", {"el_id": el_id, "text": text}, result)
        return to_json(result)

    async def navigate(url: str) -> str:
        result = await core_navigate(ctx.rt, url)
        trace(ctx, "navigate", {"url": url}, result)
        return to_json(result)

    async def screenshot() -> str:
        result = await core_screenshot(ctx.rt)
        slim = {k: v for k, v in result.items() if k != "image_b64"}
        trace(ctx, "screenshot", {}, slim)
        return to_json(slim)

    tools.extend(
        [
            StructuredTool.from_function(
                coroutine=dom_snapshot,
                name="dom_snapshot",
                description="DOM snapshot interactive elements (data-agent-id).",
            ),
            StructuredTool.from_function(
                coroutine=click_element,
                name="click_element",
                description="Клик по id из dom_snapshot.",
                args_schema=DomIdInput,
            ),
            StructuredTool.from_function(
                coroutine=fill_element,
                name="fill_element",
                description="Ввод текста в элемент по id.",
                args_schema=FillInput,
            ),
            StructuredTool.from_function(
                coroutine=navigate,
                name="navigate",
                description="Прямой переход по URL.",
                args_schema=NavigateInput,
            ),
            StructuredTool.from_function(
                coroutine=screenshot,
                name="screenshot",
                description="Скриншот viewport (для locate_on_screen).",
            ),
        ]
    )

    return tools
