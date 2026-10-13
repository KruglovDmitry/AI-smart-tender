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
from ..observability.trace import current_trace
from ..sites.profiles import remember_success
from ..storage.repository import Repository, profile_is_fresh
from ..tenders.discovery import inspect_element, pick_documents_tab
from ..tenders.extraction import apply_labeled_fields, apply_page_reading, card_from_raw
from ..tenders.models import Observation
from ..tenders.ranking import rank_tenders
from .planner import TaskParseError, consult, parse_task, read_card
from .policy import decide
from .recovery import clear_failure, note_failure, should_stop_repeat
from .vision import run_vision_step
from ..sites.known import search_url, sorted_by_date_url
from .state import Action, Progress

log = get_logger()


def search_applied(obs: Observation, keyword: str) -> bool:
    """Выдача отфильтрована по запросу: слово есть в адресе или хотя бы в трети названий.

    Основа слова (первые 5 букв) ловит «серверного», «серверов» и т. п.
    """
    from urllib.parse import unquote

    stem = keyword.casefold()[: max(4, min(len(keyword), 5))]
    if not stem or len(obs.cards) < 3:
        return True
    if stem in unquote(obs.url).casefold():
        return True
    hits = sum(1 for card in obs.cards if stem in (card.title or "").casefold())
    return hits * 3 >= len(obs.cards)


def _sync_flags(obs: Observation, progress: Progress, keyword: str, sort_required: bool) -> None:
    # Слово в поле ещё не значит, что поиск выполнен: засчитываем только на странице выдачи.
    if (
        keyword
        and not progress.keyword_applied
        and obs.page_kind in {"tender_list", "empty"}
        and search_applied(obs, keyword)
        and obs.search_value
        and obs.search_value.casefold() == keyword.casefold()
    ):
        progress.keyword_applied = True


async def _goto(runtime: BrowserRuntime, url: str, settings: Settings, host: str) -> None:
    ok, reason = validate_navigation_url(url, settings)
    if not ok:
        raise RuntimeError(reason)
    if not same_site(url, host):
        raise RuntimeError("Переход на другой сайт запрещён.")
    await runtime.current_page.goto(url, wait_until="domcontentloaded")


async def _wait_for_results(runtime: BrowserRuntime, before_url: str, timeout_ms: int = 8_000) -> None:
    """После отправки поиска ждём смены адреса и загрузки, а не фиксированные 1,5 с."""
    page = runtime.current_page
    waited = 0
    while waited < timeout_ms and page.url == before_url:
        await page.wait_for_timeout(250)
        waited += 250
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=max(1_000, timeout_ms - waited))
        await page.wait_for_load_state("networkidle", timeout=3_000)
    except Exception as exc:
        if "timeout" not in str(exc).lower():
            raise


async def _act(runtime: BrowserRuntime, action: Action, settings: Settings, host: str) -> None:
    if action.tool == "fill" and action.element_ref is not None:
        before_url = runtime.current_page.url
        await fill_field(runtime, action.element_ref, action.value or "")
        submitted = False
        if action.submit_ref is not None:
            try:
                await click_element(runtime, action.submit_ref)
                submitted = True
            except Exception as exc:  # кнопка перекрыта баннером или подсказками
                log.info("submit click failed, pressing Enter: %s", str(exc).splitlines()[0])
        if not submitted:
            await press_key(runtime, "Enter")
        await _wait_for_results(runtime, before_url)
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


def _card_needs_model(tender: TenderResult, download_documents: bool) -> bool:
    if download_documents:
        return True
    return not (
        tender.customer
        and tender.published_at
        and tender.deadline
        and tender.price is not None
    )


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


def _element_brief(obs: Observation, ref: int | None) -> dict | None:
    if ref is None:
        return None
    for el in obs.elements:
        if el.ref == ref:
            return {"ref": ref, "tag": el.tag, "name": (el.name or el.placeholder or "")[:80], "href": (el.href or "")[:120]}
    return {"ref": ref, "missing": True}


def _observation_record(obs: Observation, progress: Progress) -> dict:
    return {
        "phase": "loop",
        "url": obs.url,
        "page_kind": obs.page_kind,
        "signature": obs.signature,
        "title": obs.title[:120],
        "elements": len(obs.elements),
        "cards": len(obs.cards),
        "card_titles": [card.title[:60] for card in obs.cards[:5]],
        "fields": len(obs.fields),
        "content_chars": len(obs.content_text),
        "search": _element_brief(obs, obs.search_ref),
        "submit": _element_brief(obs, obs.submit_ref),
        "sort": _element_brief(obs, obs.sort_ref),
        "next": _element_brief(obs, obs.next_ref),
        "flags": {"keyword_applied": progress.keyword_applied, "sort_applied": progress.sort_applied,
                  "pages": progress.pages, "accepted": progress.accepted},
    }


def _action_record(action: Action, obs: Observation) -> dict:
    return {"tool": action.tool, "target": _element_brief(obs, action.element_ref), "value": action.value,
            "submit": _element_brief(obs, action.submit_ref), "note": action.note, "expected": action.expected}


async def _try_vision(runtime: BrowserRuntime, settings: Settings, task, progress: Progress,
                      reason: str, errors: list[str]) -> tuple[bool, float]:
    """Один vision-шаг, если он включён и не исчерпан. Возвращает (помог ли, сколько секунд занял)."""
    if not settings.vision_enabled or progress.vision_calls >= settings.vision_max_calls:
        return False, 0.0
    progress.vision_calls += 1
    progress.failures.clear()
    outcome = await run_vision_step(runtime, settings, task, reason, progress.keyword_applied)
    errors.append(outcome.note[:300])
    # Попадёт в запись того шага цикла, который вызвал зрение.
    progress.last_vision = {"reason": reason, "ok": outcome.ok, "note": outcome.note,
                            "seconds": round(outcome.seconds, 1), "trace": outcome.trace_dir}
    if outcome.ok:
        # Цель vision-шага — выдача по запросу (и сортировка, если её просили): дальше её не повторяем.
        progress.keyword_applied = True
        if task.sort_by:
            progress.sort_applied = True
    return outcome.ok, outcome.seconds


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
            tender = apply_labeled_fields(tender, obs.fields)
            reading = None
            need_model = _card_needs_model(tender, task.download_documents)
            if need_model and llm is not None and getattr(llm, "read_card", None):
                progress.card_reads += 1
                try:
                    reading = await read_card(llm, task, obs)
                except Exception as exc:
                    errors.append(f"Модель не прочитала карточку: {exc}")
            updated, refs = apply_page_reading(tender, obs, reading)
            card_trace = current_trace.get()
            if card_trace is not None:
                card_trace.log_step({
                    "phase": "card", "url": obs.url, "page_kind": obs.page_kind, "fields": len(obs.fields),
                    "content_chars": len(obs.content_text), "model_used": reading is not None,
                    "result": {k: getattr(updated, k) for k in ("customer", "published_at", "deadline", "price", "status")},
                    "sources": updated.sources, "document_refs": refs,
                })
                tab = pick_documents_tab(obs.elements)
                if tab is not None and progress.actions < task.max_actions:
                    await _act(
                        runtime,
                        Action(tool="click", element_ref=tab.ref, expected="changed", note="documents"),
                        settings,
                        host,
                    )
                    progress.actions += 1
                    obs = await get_page_state(runtime, progress.search_placeholder or None)
                    updated, refs = apply_page_reading(updated, obs, None)
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
            card_trace = current_trace.get()
            if card_trace is not None:
                card_trace.log_step({"phase": "card", "url": tender.url, "error": f"{type(exc).__name__}: {exc}"})
                await card_trace.snapshot(runtime.current_page, error=True)
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
    trace = current_trace.get()
    try:
        task = parse_task(text, settings)
    except TaskParseError as exc:
        return TaskResult(status="failed", errors=[str(exc)])
    if trace is not None:
        trace.task(task)
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
        step_rec: dict | None = None
        step_error = False
        try:
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
            step_rec = _observation_record(last_obs, progress)
            progress.phase = "planning"
            action = decide(last_obs, progress, task)
            step_rec["rule"] = _action_record(action, last_obs)
            if action.tool == "consult_llm":
                if progress.llm_calls >= settings.max_llm_calls:
                    errors.append("Исчерпан лимит вызовов модели.")
                    break
                progress.llm_calls += 1
                try:
                    suggested = await consult(llm, task, last_obs)
                except Exception as exc:
                    errors.append(f"Модель не выбрала действие: {exc}")
                    suggested = None
                if suggested is None:
                    step_rec["outcome"] = "no_action"
                    helped, spent = await _try_vision(
                        runtime, settings, task, progress, "незнакомая страница, DOM-правила и модель не нашли действие", errors)
                    started += spent  # время vision-шага не съедает бюджет DOM-агента
                    if helped:
                        continue
                    errors.append("Для незнакомой страницы нет правила и нет ответа модели.")
                    break
                action = suggested
            step_rec["action"] = _action_record(action, last_obs)
            if action.tool == "ask_user":
                step_rec["outcome"] = "needs_user"
                progress.phase = "needs_user"
                errors.append("Нужен вход или проверка человека. Обход не выполняется.")
                break
            if action.tool == "finish":
                step_rec["outcome"] = "finish"
                if action.note and action.expected == "error":
                    errors.append(f"Страница ошибки: {action.note}")
                elif not collected:
                    # DOM-агент сдаётся, ничего не собрав: даём шанс зрению найти поиск или выдачу.
                    helped, spent = await _try_vision(
                        runtime, settings, task, progress, "DOM-агент не нашёл поиск или выдачу закупок", errors)
                    started += spent
                    if helped:
                        continue
                break
            if (
                task.sort_by
                and progress.keyword_applied
                and not progress.sort_applied
                and not progress.sort_fallback_done
                and action.tool in {"extract", "finish"}
                and last_obs.page_kind == "tender_list"
            ):
                progress.sort_fallback_done = True
                sorted_url = sorted_by_date_url(last_obs.url)
                if sorted_url:
                    await _goto(runtime, sorted_url, settings, host)
                    progress.sort_applied = True
                    progress.site_sort_used = True
                    progress.sort_verified = True  # параметры площадки известны: первая страница и есть новейшие
                    step_rec["outcome"] = "sort_by_url"
                    continue
                helped, spent = await _try_vision(
                    runtime, settings, task, progress,
                    "нужна сортировка по дате публикации (сначала новые), кнопку сортировки в DOM не нашёл", errors)
                started += spent
                if helped:
                    progress.sort_verified = True
                    step_rec["outcome"] = "sort_by_vision"
                    continue
                errors.append("Сортировка по дате на сайте не подтверждена: «последние» выбраны только среди прочитанных записей.")
            if (
                action.tool == "fill"
                and not progress.keyword_applied
                and not progress.search_url_tried
                and progress.failures
            ):
                # Поле не отправляется из DOM — пробуем известный адрес выдачи площадки.
                known_search = search_url(last_obs.url, keyword)
                if known_search:
                    progress.search_url_tried = True
                    await _goto(runtime, known_search, settings, host)
                    step_rec["outcome"] = "search_by_url"
                    continue
            if should_stop_repeat(progress, action, last_obs.signature, settings.max_retries):
                step_rec["outcome"] = "repeat_stop"
                step_error = True
                helped, spent = await _try_vision(
                    runtime, settings, task, progress, f"действие {action.tool} повторяется без изменения страницы", errors)
                started += spent
                if helped:
                    continue
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
                    dates = [t.published_at for t in batch if t.published_at]
                    if progress.sort_applied and len(dates) >= 3 and dates == sorted(dates, reverse=True):
                        progress.sort_verified = True
                    step_rec["extracted"] = [{"id": t.id, "title": t.title[:80], "url": t.url,
                                              "status": t.verification_status} for t in batch]
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
                        titles = lambda obs: [card.title for card in obs.cards[:8]]  # noqa: E731
                        if after.url == last_obs.url and titles(after) == titles(last_obs):
                            raise RuntimeError("Поиск не применился: адрес и выдача не изменились.")
                        if not search_applied(after, keyword):
                            raise RuntimeError("Поиск не применился: в выдаче нет записей с ключевым словом.")
                        progress.keyword_applied = True
                    if action.note == "sort":
                        progress.sort_applied = True
                        progress.site_sort_used = True
                    if action.note == "sort_menu":
                        progress.sort_menu_opened = True
                    if action.note == "next":
                        progress.pages += 1
                progress.actions += 1
                clear_failure(progress, action, last_obs.signature)
                step_rec["outcome"] = step_rec.get("outcome") or "ok"
            except Exception as exc:
                progress.actions += 1
                note_failure(progress, action, last_obs.signature)
                errors.append(str(exc))
                step_rec["outcome"] = "error"
                step_rec["error"] = f"{type(exc).__name__}: {exc}"
                step_error = True
                if should_stop_repeat(progress, action, last_obs.signature, settings.max_retries):
                    helped, spent = await _try_vision(
                        runtime, settings, task, progress, f"действие {action.tool} не удалось: {exc}", errors)
                    started += spent
                    if helped:
                        continue
                    errors.append("Повтор неудачного действия остановлен.")
                    break
        finally:
            if step_rec is not None and progress.last_vision:
                step_rec["vision"] = progress.last_vision
            progress.last_vision = None
            if trace is not None and step_rec is not None:
                trace.log_step(step_rec)
                await trace.snapshot(runtime.current_page, error=step_error,
                                     text=last_obs.content_text if last_obs else "")
    else:
        if progress.actions >= task.max_actions:
            errors.append("Исчерпан лимит действий.")
        elif (time.monotonic() - started) >= task.timeout_seconds:
            errors.append("Истекло время задания.")

    # Если выдачу отсортировал сам сайт, его порядок и есть ответ: у части карточек в списке нет дат,
    # и пересортировка по ним подняла бы старые записи с датой над новыми без неё.
    order = None if progress.sort_verified else task.sort_by
    results = rank_tenders(collected, sort_by=order, limit=task.limit)
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
            "card_reads": progress.card_reads,
            "vision_calls": progress.vision_calls,
            "site_sort_used": progress.site_sort_used,
            "profile_rejected": progress.profile_rejected,
        },
    )
