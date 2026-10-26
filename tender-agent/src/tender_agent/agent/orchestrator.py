"""Задание целиком: цикл модели на выдаче, затем тот же цикл на карточке каждой найденной закупки.

1. Выдача: найти выдачу по запросу (и отсортировать), прочитать записи, при нехватке — листать.
2. Карточки (agent/cards.py): поля, лоты и документы; файлы качаются, если скачивание включено.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import date, timedelta

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
from ..tenders.reading import closed_after_card, read_results
from . import actions
from .cards import CardReader
from .resolve import resolve_links
from .loop import Goal, Loop
from .planner import TaskParseError, parse_task
from .state import Progress

log = get_logger()


def list_goal_text(task, keyword: str) -> str:
    sort = (" Отсортируй выдачу по дате публикации или размещения, сначала новые, и проверь по датам первых "
            "записей, что сверху действительно самые новые; если нет — поменяй направление сортировки."
            if task.sort_by else "")
    # Фильтр по статусу — только если включается сразу: закрытые записи отсеивает сам агент, а борьба
    # с самодельным фильтром съедала весь бюджет (lot-online: 40 шагов без extract, Сбер-АСТ: минута на зрение).
    active = (" Нужны только закупки, по которым идёт приём заявок. Если рядом с выдачей есть простой фильтр по "
              "статусу («приём заявок», «активные») — можно включить его одним-двумя кликами; если он не "
              "включился сразу или выдача после него пустая — не трать на него шаги, вызывай extract: "
              "завершённые и отменённые записи агент отсеет сам."
              if task.active_only else "")
    return (
        f"На площадке {task.url} найди закупки по запросу «{keyword}» через поиск площадки. Вводи запрос дословно, "
        f"не меняй слово и не подбирай синонимы.{sort}{active} "
        f"Когда на экране выдача по запросу{' в нужном порядке' if task.sort_by else ''} — вызови extract. "
        f"Нужно {task.limit} записей: если после extract их меньше, перейди на следующую страницу выдачи "
        f"(или нажми «Показать ещё») и снова вызови extract, но не дальше {task.max_pages} страниц. "
        "Если закупки площадки разделены на секции (44-ФЗ, 223-ФЗ, коммерческие и т.п., иногда это отдельные "
        "сайты площадки) и в выбранной секции ничего не найдено или записей меньше, чем нужно, — проверь остальные "
        "секции закупок (не продажи и не имущество) тем же запросом и собери записи из всех. "
        "Когда записей достаточно или все секции проверены — done. "
        "Карточки закупок открывать не нужно: их прочитают отдельно."
    )


STALE_DAYS = 7


def _older_than_found(page_dates: list[str], found: list[TenderResult]) -> bool:
    """Страница уже заметно старше найденного: самая свежая её запись на неделю старше самой свежей
    подходящей (или сегодняшнего дня, если подходящих нет). На ЕИС по частым словам (картриджи)
    закрытые закупки идут и среди сегодняшних — это ещё не «дальше только старые»."""
    if not page_dates:
        return False
    newest = max((t.published_at for t in found if t.published_at), default=date.today().isoformat())
    limit = (date.fromisoformat(newest) - timedelta(days=STALE_DAYS)).isoformat()
    return max(page_dates) < limit


def _descending(dates: list[str]) -> bool:
    return len(dates) >= 3 and dates == sorted(dates, reverse=True)


# Сортировка «сначала новые» в адресе выдачи: Росэлторг sort=date_published_down, Портал поставщиков
# sortDesc=true. Нужна, когда в строках выдачи нет даты публикации (Росэлторг — только срок подачи).
_SORT_DESC_URL = re.compile(r"sort\w*=[\w.]*(?:desc|down)\b|sortdesc=true", re.I)
CLOSED_TAIL = 5  # закрытых подряд после последней подходящей — дальше по дате только они


def _closed_tail(tail: list[str], found: list[TenderResult]) -> bool:
    """Хвост страницы — закрытые закупки подряд, и уже на неделю старше найденного: дальше листать незачем."""
    return len(tail) >= CLOSED_TAIL and _older_than_found(tail, found)


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
    seen_numbers: list[str] = []  # все номера, что уже прочитала модель (и отклонённые тоже)
    pages_read: list[str] = []
    site_sorted = False
    stale_pages = 0  # страниц подряд без новых подходящих, но с закрытыми — при сортировке по дате
    idle_reads = 0  # повторных чтений той же страницы без новых записей подряд

    def unique() -> int:
        return len(rank_tenders(collected, sort_by=None, limit=10_000))

    async def extract(obs: Observation) -> str:
        nonlocal site_sorted, stale_pages, idle_reads
        progress.llm_calls += 1
        try:
            reading = await read_results(llm, obs, keyword=keyword, sort_required=bool(task.sort_by),
                                         active_only=task.active_only, skip=seen_numbers)
        except Exception as exc:
            progress.mark("list", "llm", False)
            errors.append(f"Модель не прочитала выдачу: {type(exc).__name__}: {exc}")
            return f"extract → ошибка чтения: {type(exc).__name__}: {str(exc)[:200]}"
        before = unique()
        resolve_note = ""
        need = task.limit - len(rank_tenders(collected + reading.accepted, sort_by=None, limit=10_000))
        if reading.pending and need > 0:
            # Строки без ссылок (Росатом): адрес карточки узнаём кликом, только для нужного числа записей.
            resolved, resolve_note = await resolve_links(runtime, obs, reading.pending, need, host,
                                                         restore=loop.restore_search)
            progress.actions += 2 * len(resolved)
            reading.accepted.extend(resolved)
        collected.extend(reading.accepted)
        seen_numbers.extend(n for n in reading.numbers if n not in seen_numbers)
        # Та же страница прочитана снова и ничего не добавила (Фабрикант: 2 записи с фильтром, пять extract
        # подряд и две минуты до таймаута) — выдача прочитана.
        idle_reads = idle_reads + 1 if obs.signature in pages_read and unique() == before else 0
        if obs.signature not in pages_read:
            pages_read.append(obs.signature)
        if len(pages_read) == 1 and (_descending(reading.published) or _SORT_DESC_URL.search(obs.url)):
            site_sorted = True  # даты первой страницы идут по убыванию — порядок задал сайт
        progress.mark("list", "llm", bool(reading.accepted))
        rejected = ", ".join(f"{reason}: {count}" for reason, count in reading.rejected.items())
        stale = ""
        stale_pages = stale_pages + 1 if (site_sorted and reading.found and unique() == before
                                          and reading.rejected.get("приём заявок закончен")
                                          and _older_than_found(reading.published, collected)) else 0
        if site_sorted and _closed_tail(reading.closed_tail, collected):
            stale_pages = 2  # хвост этой же страницы — одни закрытые: следующая страница ещё старше
        if stale_pages:
            # Выдача по дате, а новых подходящих нет и попадаются закрытые: дальше только старее.
            stale = ("; подходящих новых записей нет, а закрытые уже пошли — дальше по дате только более старые "
                     "закупки: если выдача отсортирована по дате, заверши (done)")
        return (f"extract → записей на странице {reading.found}, принято {len(reading.accepted)}"
                f"{f' (отклонено — {rejected})' if rejected else ''}, новых {unique() - before}, всего {unique()}"
                f"{f'; {resolve_note}' if resolve_note else ''}{stale}")

    def progress_line() -> str:
        return f"Прочитано записей: {unique()} из {task.limit}; страниц выдачи прочитано: {len(pages_read)}."

    loop = Loop(runtime=runtime, settings=settings, task=task, llm=llm, host=host, progress=progress,
                errors=errors, deadline=started + task.timeout_seconds, phase="list", max_steps=task.max_actions,
                vision_limit=settings.vision_max_calls)
    def exhausted() -> str | None:
        # Две страницы подряд по дате — одни закрытые: модель на Сбер-АСТ листала их до таймаута.
        if stale_pages >= 2:
            return "Дальше по дате только закрытые закупки — выдача исчерпана."
        return "Страница выдачи прочитана повторно — новых записей нет." if idle_reads >= 2 else None

    outcome = await loop.run(Goal(text=list_goal_text(task, keyword), extract=extract, status=progress_line,
                                  context=f"площадка закупок {task.url}, запрос «{keyword}»", finished=exhausted))
    if outcome.outcome in {"timeout", "actions", "model_error"}:
        errors.append(outcome.note)

    results = rank_tenders(collected, sort_by=None if site_sorted else task.sort_by, limit=task.limit)
    cards = CardReader(runtime=runtime, settings=settings, task=task, llm=llm, host=host, progress=progress,
                       errors=errors, download=task.download_documents)
    if results and outcome.outcome != "needs_user":
        results = await cards.read_all(results)
        if task.active_only:
            # Срок подачи часто виден только на карточке (Фабрикант: в выдаче его нет), способ закупки — тоже
            # (Росатом: «Прямая закупка у ЕП» в статусе «Опубликована») — проверяем ещё раз.
            today = date.today().isoformat()
            late = [t for t in results if closed_after_card(t, today)]
            if late:
                results = [t for t in results if t not in late]
                errors.append(f"Отброшено после чтения карточки, приём заявок закончен или закупка у единственного поставщика: {len(late)}.")
        locked = sum(1 for t in results if t.details.get("access_block") == "login")
        checked = sum(1 for t in results if t.details.get("access_block") == "captcha")
        if locked:
            errors.append(f"Документы доступны только в личном кабинете площадки: {locked} из {len(results)} закупок.")
        if checked:
            errors.append(f"Площадка требует пройти проверку «я не робот» вручную: {checked} из {len(results)} закупок.")

    if outcome.outcome == "needs_user":
        status = "needs_user"
        errors.append(f"Нужен человек: {outcome.note}" if outcome.note else "Нужен вход или проверка человека.")
    elif not results and outcome.empty:
        status = "partial"
        errors.append("По применённому запросу ничего не найдено. Это не вывод обо всей площадке.")
    elif not results and outcome.outcome == "done" and task.active_only and (seen_numbers or collected):
        # Выдача прочитана, но все закупки закрыты (Росатом «ИБП»): это ответ, а не сбой.
        status = "partial"
        errors.append("Закупок с открытым приёмом заявок по этому запросу нет.")
    elif not results:
        status = "failed"
        if outcome.outcome == "done" and outcome.note:
            errors.append(f"Модель: {outcome.note}")
    elif len(results) < task.limit:
        status = "partial"
        # Обычно это не сбой: активных закупок по запросу на площадке столько и есть.
        errors.append(f"Найдено {len(results)} из {task.limit}" + (
            ": больше закупок с открытым приёмом заявок по этому запросу агент не нашёл."
            if task.active_only else "."))
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
