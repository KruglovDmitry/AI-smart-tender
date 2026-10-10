from __future__ import annotations

import asyncio
import time

from ..api.schemas import TaskResult, TenderResult
from ..browser.dom_tools import click_element, fill_field, go_back, press_key
from ..browser.downloads import download_file
from ..browser.navigation import same_site, task_host, validate_navigation_url
from ..browser.observations import get_page_state
from ..browser.runtime import BrowserRuntime
from ..browser.verification import page_changed
from ..config import Settings
from ..llm.base import LLMClient
from ..observability.logging import get_logger
from ..sites.profiles import remember_success
from ..storage.repository import Repository, profile_is_fresh
from ..tenders.discovery import inspect_element
from ..tenders.extraction import apply_page_reading, card_from_raw
from ..tenders.models import Observation
from ..tenders.ranking import rank_tenders
from .planner import TaskParseError, consult, parse_task, read_card
from .policy import decide
from .recovery import clear_failure, note_failure, should_stop_repeat
from .state import Action, Progress

log = get_logger()


def _sync_flags(obs: Observation, progress: Progress, keyword: str, sort_required: bool) -> None:
    if (
        keyword
        and not progress.keyword_applied
        and obs.search_value
        and obs.search_value.casefold() == keyword.casefold()
    ):
        progress.keyword_applied = True
    if sort_required and progress.keyword_applied and not progress.sort_applied and obs.sort_ref is None:
        progress.sort_applied = True


async def _goto(runtime: BrowserRuntime, url: str, settings: Settings, host: str) -> None:
    ok, reason = validate_navigation_url(url, settings)
    if not ok:
        raise RuntimeError(reason)
    if not same_site(url, host):
        raise RuntimeError("Переход на другой сайт запрещён.")
    await runtime.current_page.goto(url, wait_until="domcontentloaded")


async def _act(runtime: BrowserRuntime, action: Action, settings: Settings, host: str) -> None:
    if action.tool == "fill" and action.element_ref is not None:
        await fill_field(runtime, action.element_ref, action.value or "")
        if action.submit_ref is not None:
            await click_element(runtime, action.submit_ref)
        else:
            await press_key(runtime, "Enter")
            try:
                await runtime.current_page.wait_for_load_state("domcontentloaded", timeout=1_500)
            except Exception as exc:
                if "timeout" not in str(exc).lower():
                    raise
        return
    if action.tool == "click" and action.element_ref is not None:
        await click_element(runtime, action.element_ref)
        if not same_site(runtime.current_page.url, host):
            await go_back(runtime)
            raise RuntimeError("Действие увело на другой сайт.")
        return
    if action.tool == "scroll":
        from ..browser.dom_tools import scroll_page

        await scroll_page(runtime)


def _absorb(obs: Observation, task_keyword: str, keyword_applied: bool, sort_required: bool) -> list[TenderResult]:
    found: list[TenderResult] = []
    for raw in obs.cards:
        card = card_from_raw(
            raw,
            keyword=task_keyword,
            keyword_applied=keyword_applied,
            sort_required=sort_required,
        )
        if card is not None:
            found.append(card)
    return found


async def _open_cards(
    runtime: BrowserRuntime,
    settings: Settings,
    host: str,
    task,
    results: list[TenderResult],
    progress: Progress,
    llm: LLMClient | None,
    started: float,
    errors: list[str],
) -> tuple[list[TenderResult], list]:
    opened: list[TenderResult] = []
    saved = []
    for tender in results:
        if progress.actions >= task.max_actions or (time.monotonic() - started) >= task.timeout_seconds:
            opened.append(tender)
            continue
        if not tender.url:
            opened.append(tender)
            continue
        try:
            if runtime.current_page.url != tender.url:
                await _goto(runtime, tender.url, settings, host)
                progress.actions += 1
            obs = await get_page_state(runtime, progress.search_placeholder or None)
            reading = None
            if llm is not None and getattr(llm, "read_card", None) and progress.llm_calls < settings.max_llm_calls:
                progress.llm_calls += 1
                try:
                    reading = await read_card(llm, task, obs)
                except Exception as exc:
                    errors.append(f"Модель не прочитала карточку: {exc}")
            updated, refs = apply_page_reading(tender, obs, reading)
            if task.download_documents:
                for ref in refs:
                    if len(saved) >= settings.max_downloads or progress.actions >= task.max_actions:
                        break
                    saved.append(await download_file(runtime, obs, ref, settings, tender.url))
                    progress.actions += 1
                    obs = await get_page_state(runtime, progress.search_placeholder or None)
            opened.append(updated)
        except Exception as exc:
            errors.append(str(exc))
            opened.append(tender)
    return opened, saved


async def execute(
    text: str,
    *,
    settings: Settings,
    runtime: BrowserRuntime,
    repo: Repository,
    llm: LLMClient | None = None,
) -> TaskResult:
    started = time.monotonic()
    try:
        task = parse_task(text, settings)
    except TaskParseError as exc:
        return TaskResult(status="failed", errors=[str(exc)])
    ok, reason = validate_navigation_url(task.url, settings)
    if not ok:
        return TaskResult(status="failed", task=task, errors=[reason])
    host = task_host(task.url)
    progress = Progress()
    collected: list[TenderResult] = []
    errors: list[str] = []
    last_obs: Observation | None = None
    profile = repo.load_profile(host)
    profile_checked = False
    keyword = task.keywords[0]

    try:
        await asyncio.wait_for(
            _goto(runtime, task.url, settings, host),
            timeout=task.timeout_seconds,
        )
    except Exception as exc:
        return TaskResult(status="failed", task=task, errors=[str(exc)])
    progress.visited.append(task.url)

    while progress.actions < task.max_actions and (time.monotonic() - started) < task.timeout_seconds:
        progress.phase = "observing"
        last_obs = await get_page_state(runtime, (profile.search_placeholder if profile else "") or progress.search_placeholder or None)
        if last_obs.url not in progress.visited:
            progress.visited.append(last_obs.url)
        if profile and not profile_checked and profile.card_selector and last_obs.page_kind == "tender_list":
            count = await runtime.current_page.locator(profile.card_selector).count()
            profile_checked = True
            if not profile_is_fresh(last_obs.page_kind, count):
                repo.add_error(host)
                progress.profile_rejected = True
                profile = None
        _sync_flags(last_obs, progress, keyword, bool(task.sort_by))
        progress.phase = "planning"
        action = decide(last_obs, progress, task)
        if action.tool == "consult_llm":
            if progress.llm_calls >= settings.max_llm_calls:
                errors.append("Исчерпан лимит вызовов модели.")
                break
            progress.llm_calls += 1
            try:
                suggested = await consult(llm, task, last_obs)
            except Exception as exc:
                errors.append(f"Модель не выбрала действие: {exc}")
                break
            if suggested is None:
                errors.append("Для незнакомой страницы нет правила и нет ответа модели.")
                break
            action = suggested
        if action.tool == "ask_user":
            progress.phase = "needs_user"
            errors.append("Нужен вход или проверка человека. Обход не выполняется.")
            break
        if action.tool == "finish":
            if action.note and action.expected == "error":
                errors.append(f"Страница ошибки: {action.note}")
            break
        if should_stop_repeat(progress, action, last_obs.signature, settings.max_retries):
            errors.append("Одно и то же действие повторяется без изменения страницы.")
            break
        progress.phase = "executing"
        log.info("action %s url=%s", action.tool, last_obs.url.split("?")[0])
        try:
            if action.tool == "extract":
                progress.phase = "extracting"
                batch = _absorb(last_obs, keyword, progress.keyword_applied, bool(task.sort_by))
                collected.extend(batch)
                progress.accepted = len(rank_tenders(collected, sort_by=task.sort_by, limit=10_000))
                progress.extracted.append(last_obs.signature)
                progress.pages = max(progress.pages, 1)
                search = inspect_element(last_obs, last_obs.search_ref)
                if search and search.placeholder:
                    progress.search_placeholder = search.placeholder
            else:
                await _act(runtime, action, settings, host)
                after = await get_page_state(runtime, progress.search_placeholder or None)
                progress.phase = "verifying"
                if action.expected == "changed" and not page_changed(last_obs, after):
                    raise RuntimeError("Страница не изменилась.")
                if action.tool == "fill":
                    progress.keyword_applied = True
                if action.note == "sort":
                    progress.sort_applied = True
                    progress.site_sort_used = True
                if action.note == "next":
                    progress.pages += 1
            progress.actions += 1
            clear_failure(progress, action, last_obs.signature)
        except Exception as exc:
            progress.actions += 1
            note_failure(progress, action, last_obs.signature)
            errors.append(str(exc))
            if should_stop_repeat(progress, action, last_obs.signature, settings.max_retries):
                errors.append("Повтор неудачного действия остановлен.")
                break
    else:
        if progress.actions >= task.max_actions:
            errors.append("Исчерпан лимит действий.")
        elif (time.monotonic() - started) >= task.timeout_seconds:
            errors.append("Истекло время задания.")

    results = rank_tenders(collected, sort_by=task.sort_by, limit=task.limit)
    downloads = []
    if results and progress.phase != "needs_user":
        progress.phase = "downloading" if task.download_documents else "extracting"
        results, downloads = await _open_cards(
            runtime,
            settings,
            host,
            task,
            results,
            progress,
            llm,
            started,
            errors,
        )

    pages_left = bool(last_obs and last_obs.next_ref and progress.pages >= task.max_pages)
    if progress.phase == "needs_user" or any("Нужен вход" in item for item in errors):
        status = "needs_user"
    elif not results and last_obs and last_obs.page_kind == "empty":
        status = "partial"
        errors.append("По применённому запросу ничего не найдено. Это не вывод обо всей площадке.")
    elif not results:
        status = "failed"
        progress.phase = "failed"
    elif pages_left or len(results) < task.limit:
        status = "partial"
        if len(results) < task.limit:
            errors.append(f"Найдено {len(results)} из {task.limit}.")
        if pages_left:
            errors.append("Достигнут лимит страниц, на следующих страницах записи могли остаться.")
    else:
        status = "completed"
        progress.phase = "completed"
    if task.download_documents and results and status == "completed":
        if not downloads or any(item.status != "saved" for item in downloads):
            status = "partial"
            errors.append("Не все запрошенные файлы скачаны.")
    if status in {"completed", "partial"} and results:
        remember_success(repo, host, progress.search_placeholder, any(item.sources.get("title") == "dom" for item in results))
    repo.add_run(task.url, status, progress.actions, progress.llm_calls, len(results))
    unique_errors: list[str] = []
    for item in errors:
        if item and item not in unique_errors:
            unique_errors.append(item)
    return TaskResult(
        status=status,  # type: ignore[arg-type]
        task=task,
        results=results,
        downloads=downloads,
        errors=unique_errors,
        stats={
            "pages_visited": len(dict.fromkeys(progress.visited)),
            "actions": progress.actions,
            "llm_calls": progress.llm_calls,
            "site_sort_used": progress.site_sort_used,
            "profile_rejected": progress.profile_rejected,
        },
    )
