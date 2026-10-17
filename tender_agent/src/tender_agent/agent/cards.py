"""Шаг 2: карточки закупок — тот же цикл, что на выдаче, с другой целью.

Каждая закупка из ответа открывается в своей вкладке (до CARD_CONCURRENCY одновременно).
Цель цикла — прочитать поля и найти документы; если файлов на карточке нет, модель сама
ищет вкладку «Документы» кликами. ``extract`` на карточке принимает только цитаты со страницы
и, если скачивание включено, сразу скачивает найденные файлы — пока страница та же.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections import Counter

from ..api.schemas import DownloadRecord, TaskSpec, TenderResult
from ..browser.downloads import download_file
from ..browser.navigation import same_site, task_host
from ..browser.runtime import BrowserRuntime
from ..config import Settings
from ..llm.base import LLMClient
from ..observability.trace import current_trace
from ..tenders.card_reading import accept_card, read_card
from ..tenders.models import Observation
from . import actions
from .loop import Goal, Loop
from .state import Progress

CARD_CONCURRENCY = 5


class _TabRuntime:
    """Отдельная вкладка того же браузера: у каждой карточки свой цикл."""

    def __init__(self, base: BrowserRuntime, page, tender: TenderResult) -> None:
        self.settings = base.settings
        # Своя папка на закупку: у разных закупок одинаковые имена файлов («Проект контракта.docx»).
        key = tender.id or hashlib.sha1((tender.url or "").encode("utf-8")).hexdigest()[:12]
        self.downloads_dir = base.downloads_dir / re.sub(r"[^\w.\-]+", "_", key)[:80]
        self.context = base.context
        self.page = page

    @property
    def current_page(self):
        return self.page


def card_goal_text(tender: TenderResult) -> str:
    return (
        f"Открыта карточка закупки «{(tender.title or '')[:150]}» ({tender.url}). "
        "Эта страница уже прочитана (extract в истории, шаг 0). Если документов пока нет, найди на этой "
        "карточке вкладку или раздел с документами (документация, файлы, извещение, вложения), открой его и "
        "вызови extract. Если важных полей нет на этой странице, а есть вкладка со сведениями — открой её и "
        "вызови extract. Не переходи к другим закупкам и не возвращайся к выдаче. "
        "Если документы (или вся карточка) доступны только после входа в личный кабинет — сразу ask_user "
        "с пояснением: входить не нужно, искать обходные пути тоже. "
        "Когда поля прочитаны и документы найдены (или на карточке их точно нет) — done."
    )


class CardReader:
    def __init__(self, *, runtime: BrowserRuntime, settings: Settings, task: TaskSpec, llm: LLMClient, host: str,
                 progress: Progress, errors: list[str], download: bool) -> None:
        self.runtime = runtime
        self.settings = settings
        self.task = task
        self.llm = llm
        self.host = host
        self.progress = progress
        self.errors = errors
        self.download = download
        self.downloads: list[DownloadRecord] = []
        self.outcomes: Counter[str] = Counter()

    async def read_all(self, tenders: list[TenderResult]) -> list[TenderResult]:
        done = await self._read_many(tenders, CARD_CONCURRENCY)
        failed = {"error", "needs_user"}
        if len(done) >= 2 and all(outcome in failed for _, outcome in done):
            # Все карточки разом упали — похоже на лимит площадки на число вкладок одной сессии
            # (Росатом: «Превышено максимальное количество одновременно открытых вкладок»).
            # Перечитываем по одной; первая попытка в учёт не идёт.
            for _, outcome in done:
                self.outcomes[outcome] -= 1
            self.outcomes["sequential_retry"] += 1
            # По одной и в той же вкладке, где была выдача: портал считает вкладки сессии, и даже
            # последовательно открытые новые вкладки упираются в лимит (проверено на Росатоме).
            done = [await self._read_in_tab(tender.model_copy(deep=True), reuse_main=True) for tender in tenders]
        return [tender for tender, _ in done]

    async def _read_many(self, tenders: list[TenderResult], concurrency: int) -> list[tuple[TenderResult, str]]:
        limit = asyncio.Semaphore(concurrency)

        async def one(tender: TenderResult) -> tuple[TenderResult, str]:
            async with limit:
                # Копия: повторная попытка начинается с записи из выдачи, без следов первой.
                return await self._read_in_tab(tender.model_copy(deep=True))

        return list(await asyncio.gather(*(one(tender) for tender in tenders)))

    async def _read_in_tab(self, tender: TenderResult, reuse_main: bool = False) -> tuple[TenderResult, str]:
        if not tender.url:
            return tender, "skipped"
        if reuse_main:
            page = self.runtime.current_page
        else:
            page = await self.runtime.context.new_page()
            page.set_default_timeout(15_000)
            page.set_default_navigation_timeout(45_000)
        tab = _TabRuntime(self.runtime, page, tender)
        try:
            return await self._read(tab, tender)
        except Exception as exc:
            self.outcomes["error"] += 1
            self.errors.append(f"Карточка {tender.url} не прочитана: {str(exc).splitlines()[0][:200]}")
            trace = current_trace.get()
            if trace is not None:
                trace.log_step({"phase": "card", "url": tender.url, "error": f"{type(exc).__name__}: {exc}"})
                await trace.snapshot(page, error=True)
            return tender, "error"
        finally:
            if not reuse_main:
                await page.close()

    async def _read(self, tab: _TabRuntime, tender: TenderResult) -> tuple[TenderResult, str]:
        # Карточка открывается там, куда ведёт ссылка из выдачи, даже если это другой сайт (ЕИС):
        # выдача площадки ссылается на закупки, опубликованные в другом месте. Переходы внутри
        # карточки — в пределах её сайта.
        card_host = task_host(tender.url or "")
        await actions.goto(tab, tender.url or "", self.settings, card_host)
        await actions.settle(tab)
        self.progress.actions += 1
        state = {"tender": tender}
        if not same_site(tender.url or "", self.host):
            tender.details["source"] = card_host.removeprefix("www.")

        async def extract(obs: Observation) -> str:
            self.progress.llm_calls += 1
            try:
                data = await read_card(self.llm, obs, keyword=self.task.keywords[0])
            except Exception as exc:
                self.progress.mark("card_fields", "llm", False)
                self.errors.append(f"Модель не прочитала карточку: {type(exc).__name__}: {exc}")
                return f"extract → ошибка чтения: {type(exc).__name__}: {str(exc)[:200]}"
            reading = accept_card(state["tender"], obs, data)
            state["tender"] = reading.tender
            self.progress.mark("card_fields", "llm", bool(reading.filled))
            self.progress.mark("documents_files", "llm", bool(reading.refs))
            note = (f"extract → полей добавлено: {', '.join(reading.filled) or 'нет'}; "
                    f"документов на странице: {len(reading.refs)}; всего документов: {len(reading.tender.documents)}")
            if reading.rejected:
                note += f"; отклонено цитат, которых нет на странице: {reading.rejected}"
            if self.download and reading.refs:
                note += await self._download(tab, obs, reading.refs, tender.url)
            return note

        def status() -> str:
            current = state["tender"]
            filled = [name for name in ("customer", "published_at", "deadline", "price", "status") if getattr(current, name)]
            return f"Поля карточки: {', '.join(filled) or 'пока нет'}; документов найдено: {len(current.documents)}."

        loop = Loop(runtime=tab, settings=self.settings, task=self.task, llm=self.llm, host=card_host,
                    progress=self.progress, errors=self.errors,
                    deadline=time.monotonic() + self.settings.card_timeout_seconds, phase="card",
                    max_steps=self.settings.card_max_steps, vision_limit=1, vision_extends_deadline=False)
        outcome = await loop.run(Goal(
            text=card_goal_text(tender), extract=extract, status=status,
            context=f"карточка закупки «{(tender.title or '')[:120]}» на {card_host}", extract_first=True,
            unmet=lambda: None if state["tender"].documents else (
                "Найди на этой карточке документы закупки (файлы документации, извещение, проект контракта) "
                "и сделай их видимыми: открой вкладку или раскрой блок с документами"),
        ))
        self.outcomes[outcome.outcome] += 1
        if outcome.outcome == "needs_user":
            # Документы за входом в личный кабинет: закупка остаётся в ответе, в карточке — пометка.
            state["tender"].details["documents_access"] = (outcome.note or "только после входа в личный кабинет")[:300]
        return state["tender"], outcome.outcome

    async def _download(self, tab: _TabRuntime, obs: Observation, refs: list[int], tender_url: str | None) -> str:
        saved = 0
        tried = 0
        # Лимит — на закупку, а не на задание: закупка с десятками файлов не отнимает место у остальных.
        done_here = sum(1 for item in self.downloads if item.tender_url == tender_url)
        for ref in refs:
            if done_here + tried >= self.settings.max_downloads:
                break
            record = await download_file(tab, obs, ref, self.settings, tender_url)
            self.downloads.append(record)
            self.progress.actions += 1
            tried += 1
            saved += record.status == "saved"
        self.progress.mark("download", "rule", saved > 0)
        skipped = len(refs) - tried
        return f"; скачано {saved} из {tried}" + (f"; пропущено по лимиту файлов на закупку: {skipped}" if skipped else "")
