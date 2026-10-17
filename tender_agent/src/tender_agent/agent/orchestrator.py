"""Задание целиком: цикл модели на выдаче, затем тот же цикл на карточке каждой найденной закупки.

1. Выдача: найти выдачу по запросу (и отсортировать), прочитать записи, при нехватке — листать.
2. Карточки (agent/cards.py): поля, лоты и документы; файлы качаются, если скачивание включено.
"""

from __future__ import annotations

import asyncio
import time

from ..api.schemas import TaskResult, TenderResult
from ..browser.navigation import task_host, validate_navigation_url
from ..browser.runtime import BrowserRuntime
from ..config import Settings
from ..llm.base import LLMClient
from ..observability.logging import get_logger
from ..observability.trace import current_trace
from ..storage.repository import Repository
from ..tenders.models import Observation
from ..tenders.ranking import rank_tenders
from ..tenders.reading import read_results
from . import actions
from .cards import CardReader
from .loop import Goal, Loop
from .planner import TaskParseError, parse_task
from .state import Progress

log = get_logger()


def list_goal_text(task, keyword: str) -> str:
    sort = " Отсортируй выдачу по дате публикации или размещения, сначала новые." if task.sort_by else ""
    return (
        f"На площадке {task.url} найди закупки по запросу «{keyword}» через поиск площадки.{sort} "
        f"Когда на экране выдача по запросу{' в нужном порядке' if task.sort_by else ''} — вызови extract. "
        f"Нужно {task.limit} записей: если после extract их меньше, перейди на следующую страницу выдачи "
        f"(или нажми «Показать ещё») и снова вызови extract, но не дальше {task.max_pages} страниц. "
        "Когда записей достаточно — done. Карточки закупок открывать не нужно: их прочитают отдельно."
    )


def _descending(batch: list[TenderResult]) -> bool:
    dates = [t.published_at for t in batch if t.published_at]
    return len(dates) >= 3 and dates == sorted(dates, reverse=True)


async def execute(
    text: str,
    *,
    settings: Settings,
    runtime: BrowserRuntime,
    repo: Repository,
    llm: LLMClient | None = None,
    download: bool | None = None,
) -> TaskResult:
    """download — скачивать ли документы; None — как сказано в задании («скачай» / «не скачивай»).
    Общий выключатель Settings.downloads_enabled сильнее и того, и другого."""
    started = time.monotonic()
    trace = current_trace.get()
    try:
        task = parse_task(text, settings)
    except TaskParseError as exc:
        return TaskResult(status="failed", errors=[str(exc)])
    if download is not None:
        task.download_documents = download
    task.download_documents = task.download_documents and settings.downloads_enabled
    if trace is not None:
        trace.task(task)
    ok, reason = validate_navigation_url(task.url, settings)
    if not ok:
        return TaskResult(status="failed", task=task, errors=[reason])
    if llm is None:
        return TaskResult(status="failed", task=task, errors=["Модель не настроена: агент без неё не работает."])
    host = task_host(task.url)
    progress = Progress()
    errors: list[str] = []
    try:
        await asyncio.wait_for(actions.goto(runtime, task.url, settings, host), timeout=task.timeout_seconds)
        await actions.settle(runtime)
    except Exception as exc:
        return TaskResult(status="failed", task=task, errors=[str(exc)])

    keyword = task.keywords[0]
    collected: list[TenderResult] = []
    pages_read: list[str] = []
    site_sorted = False

    def unique() -> int:
        return len(rank_tenders(collected, sort_by=None, limit=10_000))

    async def extract(obs: Observation) -> str:
        nonlocal site_sorted
        progress.llm_calls += 1
        try:
            reading = await read_results(llm, obs, keyword=keyword, sort_required=bool(task.sort_by))
        except Exception as exc:
            progress.mark("list", "llm", False)
            errors.append(f"Модель не прочитала выдачу: {type(exc).__name__}: {exc}")
            return f"extract → ошибка чтения: {type(exc).__name__}: {str(exc)[:200]}"
        before = unique()
        collected.extend(reading.accepted)
        if obs.signature not in pages_read:
            pages_read.append(obs.signature)
        if len(pages_read) == 1 and _descending(reading.accepted):
            site_sorted = True  # даты первой страницы идут по убыванию — порядок задал сайт
        progress.mark("list", "llm", bool(reading.accepted))
        rejected = ", ".join(f"{reason}: {count}" for reason, count in reading.rejected.items())
        return (f"extract → записей на странице {reading.found}, принято {len(reading.accepted)}"
                f"{f' (отклонено — {rejected})' if rejected else ''}, новых {unique() - before}, всего {unique()}")

    def progress_line() -> str:
        return f"Прочитано записей: {unique()} из {task.limit}; страниц выдачи прочитано: {len(pages_read)}."

    loop = Loop(runtime=runtime, settings=settings, task=task, llm=llm, host=host, progress=progress,
                errors=errors, deadline=started + task.timeout_seconds, phase="list", max_steps=task.max_actions,
                vision_limit=settings.vision_max_calls)
    outcome = await loop.run(Goal(text=list_goal_text(task, keyword), extract=extract, status=progress_line,
                                  context=f"площадка закупок {task.url}, запрос «{keyword}»"))
    if outcome.outcome in {"timeout", "actions", "model_error"}:
        errors.append(outcome.note)

    results = rank_tenders(collected, sort_by=None if site_sorted else task.sort_by, limit=task.limit)
    cards = CardReader(runtime=runtime, settings=settings, task=task, llm=llm, host=host, progress=progress,
                       errors=errors, download=task.download_documents)
    if results and outcome.outcome != "needs_user":
        results = await cards.read_all(results)
        locked = sum(1 for t in results if t.details.get("documents_access"))
        if locked:
            errors.append(f"Документы доступны только в личном кабинете площадки: {locked} из {len(results)} закупок.")

    if outcome.outcome == "needs_user":
        status = "needs_user"
        errors.append(f"Нужен человек: {outcome.note}" if outcome.note else "Нужен вход или проверка человека.")
    elif not results and outcome.empty:
        status = "partial"
        errors.append("По применённому запросу ничего не найдено. Это не вывод обо всей площадке.")
    elif not results:
        status = "failed"
        if outcome.outcome == "done" and outcome.note:
            errors.append(f"Модель: {outcome.note}")
    elif len(results) < task.limit:
        status = "partial"
        errors.append(f"Найдено {len(results)} из {task.limit}.")
    else:
        status = "completed"
    if task.download_documents and status == "completed":
        if not cards.downloads or any(item.status != "saved" for item in cards.downloads):
            status = "partial"
            errors.append("Не все запрошенные файлы скачаны.")
    repo.add_run(task.url, status, progress.actions, progress.llm_calls, len(results))
    return TaskResult(
        status=status,  # type: ignore[arg-type]
        task=task,
        results=results,
        downloads=cards.downloads,
        errors=list(dict.fromkeys(item for item in errors if item)),
        stats={
            "pages_visited": len(progress.visited),
            "actions": progress.actions,
            "llm_calls": progress.llm_calls,
            "vision_calls": progress.vision_calls,
            "pages_read": len(pages_read),
            "site_sort_used": site_sorted,
            "loop_outcome": outcome.outcome,
            "card_outcomes": dict(cards.outcomes),
            "documents": sum(len(t.documents) for t in results),
            "methods": progress.methods,
        },
    )
