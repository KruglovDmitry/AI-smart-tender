"""High-level platform tools — exactly 15 tools for mode=platform."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...core.browser import dom as browser_dom
from ...core.browser.primitives import download_url as core_download
from ...core.browser.primitives import get_page_text as core_get_page_text
from ...core.browser.primitives import navigate as core_navigate
from ...core.vision import click_on_screen as vision_click
from ...core.vision import get_vision_backend
from ...core.vision import inspect_screen as vision_inspect
from ...domain import finish as finish_mod
from ...domain import manifest as manifest_mod
from ...domain import overview as overview_mod
from ...domain.tender_id import resolve_tender_id
from ...platforms.base import CardRef, SearchSpec
from ...platforms.registry import get_adapter
from ..context import (
    PlatformAgentContext,
    ensure_tender_workspace,
    note_pending_new,
    note_results_url,
    pop_pending_new,
    to_json,
    trace,
)

PLATFORM_TOOL_NAMES: tuple[str, ...] = (
    "open_platform_search",
    "list_new_cards",
    "open_tender",
    "list_tender_documents",
    "download_document",
    "save_overview",
    "mark_processed",
    "goto_next_page",
    "finish",
    "dom_snapshot",
    "click_element",
    "fill_element",
    "navigate",
    "click_on_screen",
    "inspect_screen",
)


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


class GoalInput(BaseModel):
    goal: str = Field(description="Что кликнуть на экране (на русском, без координат)")


class QuestionInput(BaseModel):
    question: str = Field(description="Вопрос по скриншоту (капча? логин? таблица?)")


class DomSnapshotInput(BaseModel):
    query: str | None = Field(
        default=None,
        description="Фильтр по тексту/placeholder/role; без query — до 150 приоритетных",
    )


class DomIdInput(BaseModel):
    el_id: int = Field(description="id из dom_snapshot")


class FillInput(BaseModel):
    el_id: int
    text: str
    submit: bool = Field(
        default=False,
        description="True — нажать Enter после ввода (отправка формы/поиска)",
    )


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
            description="Открыть поиск на площадке. ВЕРНЁТ page_kind/url.",
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
            description="Открыть карточку по exact URL.",
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
            description="Список документов текущей карточки (adapter).",
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

    async def save_overview() -> str:
        page_url = str(ctx.rt.page.url or "")
        resolved = resolve_tender_id(page_url, platform=ctx.platform)
        page_tid = str(resolved.get("tender_id") or "").strip() or None
        tender_id = page_tid or (str(ctx.current_tender_id or "").strip() or None)
        if tender_id:
            ctx.current_tender_id = tender_id
            if page_tid:
                ctx.current_tender_url = page_url
            elif not ctx.current_tender_url:
                ctx.current_tender_url = page_url
        if not tender_id:
            result = {
                "ok": False,
                "action": "save_overview",
                "message": "Нет tender_id: сначала open_tender(card_url).",
            }
            trace(ctx, "save_overview", {}, result)
            return to_json(result)

        page = await core_get_page_text(ctx.rt, 14000)
        if not page.get("ok"):
            result = {
                "ok": False,
                "action": "save_overview",
                "message": page.get("message") or "get_page_text failed",
            }
            trace(ctx, "save_overview", {}, result)
            return to_json(result)

        page_url = str(page.get("url") or ctx.rt.page.url)
        resolved2 = resolve_tender_id(page_url, platform=ctx.platform)
        page_tid2 = str(resolved2.get("tender_id") or "").strip() or None
        if page_tid2:
            tender_id = page_tid2
            ctx.current_tender_id = tender_id
            ctx.current_tender_url = page_url
        tender_url = ctx.current_tender_url or page_url
        folder = ensure_tender_workspace(ctx, tender_id, tender_url)

        try:
            payload = await overview_mod.extract_overview(
                tender_id=tender_id,
                tender_url=tender_url,
                page_url=page_url,
                page_title=str(page.get("title") or ""),
                page_text=str(page.get("text") or ""),
                platform=ctx.platform,
            )
        except Exception as e:
            result = {
                "ok": False,
                "action": "save_overview",
                "message": f"LLM overview failed: {e}",
                "tender_id": tender_id,
                "tender_dir": str(folder),
            }
            trace(ctx, "save_overview", {}, result)
            return to_json(result)

        out_path = Path(folder) / "overview.json"
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            manifest_mod.upsert_overview_fields(
                folder,
                {**payload, "platform": ctx.platform, "tender_id": tender_id},
            )
        except Exception:
            pass
        result = {
            "ok": True,
            "action": "save_overview",
            "message": f"Saved {out_path.name} into {folder}",
            "tender_id": tender_id,
            "tender_dir": str(folder),
            "overview_path": str(out_path),
            "overview": {
                "tender_url": payload.get("tender_url"),
                "object": payload.get("object"),
                "customer": payload.get("customer"),
                "price": payload.get("price"),
                "deadline": payload.get("deadline"),
                "method": payload.get("method"),
                "stage": payload.get("stage"),
                "placed_at": payload.get("placed_at"),
            },
        }
        stale, reason = overview_mod.looks_stale(payload)
        if stale:
            result["looks_stale"] = True
            result["stale_reason"] = reason
            result["hint"] = (
                "Устаревший/неактивный: mark_processed(..., count_toward_limit=false)."
            )
            result["message"] += " | looks_stale=true"
        trace(ctx, "save_overview", {}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=save_overview,
            name="save_overview",
            description="Сохранить overview.json (+ поля в manifest).",
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
            description="Следующая страница выдачи (adapter).",
        )
    )

    async def finish(summary: str, success: bool = True) -> str:
        success, summary = finish_mod.resolve_finish_success(
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
            description="Завершить задачу (структурный success).",
            args_schema=FinishInput,
            return_direct=True,
        )
    )

    async def dom_snapshot(query: str | None = None) -> str:
        result = await browser_dom.snapshot_for_agent(ctx.rt, query)
        trace(ctx, "dom_snapshot", {"query": query}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=dom_snapshot,
            name="dom_snapshot",
            description=(
                "DOM interactive elements (data-agent-id). "
                "query фильтрует; без query — до 150 (inputs→buttons→links)."
            ),
            args_schema=DomSnapshotInput,
        )
    )

    async def click_element(el_id: int) -> str:
        result = await browser_dom.click_by_id(ctx.rt, el_id)
        trace(ctx, "click_element", {"el_id": el_id}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=click_element,
            name="click_element",
            description="Клик по id из dom_snapshot (scroll into view).",
            args_schema=DomIdInput,
        )
    )

    async def fill_element(el_id: int, text: str, submit: bool = False) -> str:
        result = await browser_dom.fill_by_id(ctx.rt, el_id, text, submit=submit)
        trace(
            ctx,
            "fill_element",
            {"el_id": el_id, "text": text, "submit": submit},
            result,
        )
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=fill_element,
            name="fill_element",
            description="Ввод в элемент по id; submit=True → Enter.",
            args_schema=FillInput,
        )
    )

    async def navigate(url: str) -> str:
        result = await core_navigate(ctx.rt, url)
        trace(ctx, "navigate", {"url": url}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=navigate,
            name="navigate",
            description="Прямой переход по URL.",
            args_schema=NavigateInput,
        )
    )

    async def click_on_screen(goal: str) -> str:
        backend = get_vision_backend()
        result = await vision_click(
            ctx.rt,
            goal,
            backend=backend,
            run_id=ctx.vision_run_id or None,
            platform=ctx.platform,
        )
        # Never expose coordinates to the agent
        for k in ("x", "y", "candidates", "chosen"):
            result.pop(k, None)
        trace(ctx, "click_on_screen", {"goal": goal}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=click_on_screen,
            name="click_on_screen",
            description=(
                "Vision-fallback: найти цель на скрине, провалидировать DOM и "
                "navigate|click. Без координат в ответе. Когда DOM пуст/не помогает."
            ),
            args_schema=GoalInput,
        )
    )

    async def inspect_screen(question: str) -> str:
        backend = get_vision_backend()
        result = await vision_inspect(ctx.rt, question, backend=backend)
        trace(ctx, "inspect_screen", {"question": question}, result)
        return to_json(result)

    tools.append(
        StructuredTool.from_function(
            coroutine=inspect_screen,
            name="inspect_screen",
            description="Вопрос по скриншоту (капча/логин/таблица результатов?).",
            args_schema=QuestionInput,
        )
    )

    assert len(tools) == 15, f"expected 15 platform tools, got {len(tools)}"
    names = [t.name for t in tools]
    assert names == list(PLATFORM_TOOL_NAMES), names
    return tools
