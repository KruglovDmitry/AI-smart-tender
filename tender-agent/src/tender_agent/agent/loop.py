"""Цикл агента, как у browser-use: наблюдение → решение модели → действие → проверка.

Один и тот же цикл работает на любом этапе: меняются только цель и то, что делает
``extract`` (на выдаче — собирает записи). Модель видит цель, историю своих шагов с итогами
и снимок страницы (адрес, видимый текст, пронумерованные элементы) и выбирает одно действие.
Код выполняет его, снимает страницу заново и пишет итог в историю: изменилась ли страница,
ушли ли на другой адрес, была ли ошибка. Зрение подключается, когда модель просит его сама
или два действия подряд ничего не изменили.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlparse

from ..api.schemas import TaskSpec
from ..browser.navigation import same_site, task_host
from ..browser.runtime import BrowserRuntime
from ..browser.snapshot import get_page_state, page_changed
from ..config import Settings
from ..llm.base import LLMClient
from ..observability.trace import current_trace
from ..sites import notes_for
from ..tenders.models import Element, Observation
from . import actions
from .state import Progress
from .vision import VisionOutcome, run_vision_step, vision_goal

PAGE_TEXT_FOR_MODEL = 6_000
HISTORY_FOR_MODEL = 12
REPEAT_WINDOW, REPEAT_LIMIT = 4, 3  # 3 одинаковых действия из последних 4 — цикл
TOOLS = {"click", "fill", "scroll", "go_back", "goto", "extract", "done", "ask_user", "vision"}


@dataclass
class Goal:
    text: str  # что нужно сделать
    extract: Callable[[Observation], Awaitable[str]]  # чтение страницы; возвращает строку для истории
    status: Callable[[], str]  # прогресс для модели: «прочитано записей: 3 из 5»
    context: str = ""  # одной строкой для зрения: где мы и что ищем (без общей цели — см. vision_goal)
    # Прочитать открытую страницу до первого решения модели (карточка: иначе модель сразу уходит
    # во вкладку «Документы», и поля основной страницы остаются непрочитанными).
    extract_first: bool = False
    # Модель сказала done, а цель не достигнута (карточка без документов): что поручить зрению
    # для проверки. None — подтверждать нечего.
    unmet: Callable[[], str | None] | None = None
    # Цель исчерпана по данным, а не по решению модели (выдача по дате ушла в закрытые закупки):
    # цикл заканчивается сам, даже если модель хочет листать дальше.
    finished: Callable[[], str | None] | None = None


@dataclass
class LoopResult:
    # done | needs_user | timeout | actions | model_error
    outcome: str
    note: str = ""  # последние слова модели (done / ask_user) или причина остановки
    empty: bool = False  # площадка сообщила, что по запросу ничего нет
    last_obs: Observation | None = None
    history: list[str] = field(default_factory=list)


@dataclass
class Loop:
    runtime: BrowserRuntime
    settings: Settings
    task: TaskSpec
    llm: LLMClient
    host: str
    progress: Progress
    errors: list[str]
    deadline: float
    phase: str = "list"  # list | card — попадает в трейс
    max_steps: int = 40
    # Зрение: сколько раз можно позвать в этом цикле; продлевает ли оно бюджет времени.
    # На выдаче продлевает (не съедает время модели), на карточке — нет: у карточки жёсткий бюджет.
    vision_limit: int = 2
    vision_extends_deadline: bool = True
    vision_used: int = 0
    # Последний отправленный поиск: (поле, текст, адрес после отправки). По нему выдачу можно вернуть,
    # если сайт сбросил её после перехода в карточку и назад (Росатом) — см. restore_search.
    last_search: tuple[Element, str, str] | None = None
    # Карточка: можно перейти на другой сайт, но только по ссылке, которая есть на странице
    # (B2B-Center → market.otc.ru, агрегатор → площадка-источник). Адрес, придуманный моделью, — нельзя.
    follow_links: bool = False

    async def restore_search(self) -> bool:
        """Повторить последний поиск: поле ищется по подсказке или имени — номера после перерисовки другие."""
        if self.last_search is None:
            return False
        field_was, text, _url = self.last_search
        obs = await self.observe()
        field = next((el for el in obs.elements if el.tag in {"input", "textarea"}
                      and ((field_was.placeholder and el.placeholder == field_was.placeholder)
                           or (field_was.name and el.name == field_was.name))), None)
        field = field or next((el for el in obs.elements if el.input_type in {"text", "search"}), None)
        if field is None:
            return False
        await actions.fill(self.runtime, field.ref, text, True, self.host)
        return True

    async def observe(self) -> Observation:
        if self.runtime.current_page.url not in self.progress.visited:
            # Новая страница: дождаться, пока затихнет сеть. Портал поставщиков, Сбер-АСТ, ТЭК-Торг первые
            # секунды показывают заглушку («Войти», шапка и подвал), и модель делала вывод «нужен вход».
            await actions.settle(self.runtime, timeout_ms=4_000)
        # page.evaluate сам по себе не ограничен по времени: зависшая страница не должна держать задание.
        obs = await asyncio.wait_for(get_page_state(self.runtime), timeout=45)
        for _ in range(6):
            # Страница ещё не нарисована скриптом (Росатом: первые секунды ни текста, ни элементов) —
            # подождать, а не отдавать модели пустой экран (она зовёт зрение, это минута).
            if obs.text.strip() or obs.elements:
                break
            await self.runtime.current_page.wait_for_timeout(2_000)
            obs = await asyncio.wait_for(get_page_state(self.runtime), timeout=45)
        if obs.url not in self.progress.visited:
            self.progress.visited.append(obs.url)
        return obs

    def vision_ready(self) -> bool:
        enough_time = self.vision_extends_deadline or self.deadline - time.monotonic() >= 20
        return self.settings.vision_enabled and self.vision_used < self.vision_limit and enough_time

    async def run(self, goal: Goal) -> LoopResult:
        history: list[str] = []
        repeated: list[str] = []  # подписи действий: одинаковые подряд — цикл
        unchanged = 0
        model_failures = 0
        obs = await self.observe()
        if goal.extract_first and obs.page_kind != "captcha":
            note = await goal.extract(obs)
            history.append(f"0. {note}")
            await self._log({"phase": self.phase, "step": 0, "url": obs.url, "title": obs.title[:120],
                             "decision": {"tool": "extract", "thought": "первое чтение страницы — без решения модели"},
                             "note": note, "changed": True}, obs)
        for step in range(1, self.max_steps + 1):
            if time.monotonic() >= self.deadline:
                return LoopResult("timeout", "Истекло время задания.", last_obs=obs, history=history)
            if obs.page_kind == "captcha":
                return LoopResult("needs_user", "На странице CAPTCHA.", last_obs=obs, history=history)
            record: dict = {"phase": self.phase, "step": step, "url": obs.url, "title": obs.title[:120],
                            "elements": len(obs.elements), "text_chars": len(obs.text)}
            self.progress.llm_calls += 1
            request = {"goal": goal.text, "progress": goal.status(), "history": history[-HISTORY_FOR_MODEL:]}
            # Заметки о площадке — по адресу страницы, а не по площадке запуска: карточка могла увести
            # на площадку-источник.
            site = notes_for(obs.url, self.phase, self.settings.data_dir / "_agent" / "sites")
            if site:
                request["site_notes"] = site
            try:
                decision = await self.llm.next_action(request, page_for_model(obs))
                model_failures = 0
            except Exception as exc:
                model_failures += 1
                self.errors.append(f"Модель не выбрала действие: {type(exc).__name__}: {exc}")
                record["error"] = f"{type(exc).__name__}: {exc}"
                await self._log(record, obs, error=True)
                if model_failures >= 2:
                    return LoopResult("model_error", "Модель дважды не ответила.", last_obs=obs, history=history)
                continue
            record["decision"] = decision
            tool = str(decision.get("tool") or "")
            if tool == "done":
                await self._log(record, obs)
                need = goal.unmet() if goal.unmet else None
                if need and self.vision_ready():
                    # Модель уверена, что всё сделано, но цель не достигнута — проверяем глазами, один раз.
                    obs, note, _moved = await self._vision(goal, need, obs)
                    history.append(f"{step}. {note}")
                    note = await goal.extract(obs)
                    history.append(f"{step}. {note}")
                    await self._log({"phase": self.phase, "step": step, "url": obs.url, "confirm_vision": True,
                                     "note": note}, obs)
                    self.vision_limit = self.vision_used  # второй раз не подтверждаем
                    continue
                return LoopResult("done", str(decision.get("text") or ""), bool(decision.get("empty")), obs, history)
            if tool == "ask_user":
                await self._log(record, obs)
                return LoopResult("needs_user", str(decision.get("text") or ""), last_obs=obs, history=history)
            after, note, moved = await self._act(tool, decision, obs, goal)
            unchanged = 0 if moved else unchanged + 1
            history.append(f"{step}. {note}")
            stop = goal.finished() if goal.finished else None
            if stop:
                await self._log({**record, "note": note, "changed": moved}, obs)
                return LoopResult("done", stop, last_obs=after, history=history)
            repeated.append(re.sub(r"\[\d+\] ", "", _label(tool, decision, _element(obs, decision.get("ref")))))
            record.update({"note": note, "changed": moved})
            await self._log(record, obs, error=not moved and tool not in {"extract", "fill"})
            obs = after
            if unchanged >= 2 and self.vision_ready():
                # Два действия подряд ничего не изменили: модель по DOM застряла — зовём зрение.
                need = f"Модель не может продвинуться: {str(decision.get('thought') or '')[:300]}"
                obs, note, moved = await self._vision(goal, need, obs)
                history.append(f"{step}. {note}")
                await self._log({"phase": self.phase, "step": step, "url": obs.url, "auto_vision": True,
                                 "note": note, "changed": moved}, obs)
                unchanged = 0
            elif tool not in {"extract", "scroll"} and repeated[-REPEAT_WINDOW:].count(repeated[-1]) >= REPEAT_LIMIT:
                # Одно и то же действие снова и снова, хотя страница каждый раз «меняется» (Росэлторг без
                # зрения 16 раз открывал и закрывал «По умолчанию»): это цикл, а не продвижение.
                repeated.clear()
                need = f"Действие «{note.split(' → ')[0]}» повторено {REPEAT_LIMIT} раза без продвижения к цели"
                if self.vision_ready():
                    obs, vnote, _moved = await self._vision(goal, need, obs)
                    history.append(f"{step}. {vnote}")
                else:
                    history.append(f"{step}. {need}. Смени способ: другой элемент, адрес с нужными параметрами "
                                   "(goto) или продолжай без этого шага (например, extract без сортировки).")
        return LoopResult("actions", "Исчерпан лимит шагов.", last_obs=obs, history=history)

    async def _act(self, tool: str, decision: dict, obs: Observation, goal: Goal) -> tuple[Observation, str, bool]:
        """Выполнить действие; вернуть новый снимок, строку для истории и изменилась ли страница."""
        if tool not in TOOLS:
            return obs, f"{tool or '—'}: такого инструмента нет", False
        if tool == "extract":
            note = await goal.extract(obs)
            # extract мог ходить по странице (адреса карточек кликом, скачивание): снимок заново,
            # иначе номера элементов у модели устареют.
            return await self.observe(), note, True
        if tool == "vision":
            if not self.vision_ready():
                return obs, "vision: зрение недоступно или исчерпано, действуй по элементам страницы", False
            return await self._vision(goal, str(decision.get("text") or decision.get("thought") or ""), obs)
        element = _element(obs, decision.get("ref"))
        label = _label(tool, decision, element)
        if tool in {"click", "fill"} and element is None:
            return obs, f"{label}: элемента с таким ref нет на странице", False
        host = self._link_host(tool, decision, element, obs)
        try:
            if tool == "click":
                await actions.click(self.runtime, element.ref, host)
            elif tool == "fill":
                await actions.fill(self.runtime, element.ref, str(decision.get("text") or ""),
                                   bool(decision.get("submit")), self.host)
                if decision.get("submit"):
                    self.last_search = (element, str(decision.get("text") or ""), self.runtime.current_page.url)
            elif tool == "scroll":
                await actions.scroll(self.runtime, str(decision.get("direction") or "down"))
            elif tool == "go_back":
                await actions.back(self.runtime)
            elif tool == "goto":
                await actions.goto(self.runtime, str(decision.get("url") or ""), self.settings, host)
                await actions.settle(self.runtime)
        except Exception as exc:
            self.progress.actions += 1
            self.progress.mark("navigate", "llm", False)
            after = await self.observe()
            return after, f"{label} → ошибка: {action_error(exc)}", page_changed(obs, after)
        self.progress.actions += 1
        if host != self.host and same_site(self.runtime.current_page.url, host):
            self.host = host  # дальше работаем на площадке-источнике
        after = await self.observe()
        moved = page_changed(obs, after) or (tool == "scroll" and after.scroll.get("y") != obs.scroll.get("y"))
        if tool == "fill" and not decision.get("submit"):
            moved = True  # ввод без отправки меняет только поле
        self.progress.mark("navigate", "llm", moved)
        return after, f"{label} → {_outcome(obs, after, moved)}", moved

    def _link_host(self, tool: str, decision: dict, element: Element | None, obs: Observation) -> str:
        """Хост, на котором должно оказаться действие. Чужой — только по ссылке со страницы карточки."""
        if not self.follow_links:
            return self.host
        target = element.href if tool == "click" and element is not None else None
        if tool == "goto":
            url = str(decision.get("url") or "")
            target = url if any(el.href == url for el in obs.elements) else None
        if target and not same_site(target, self.host):
            return task_host(target)
        return self.host

    async def _vision(self, goal: Goal, need: str, obs: Observation) -> tuple[Observation, str, bool]:
        self.vision_used += 1
        self.progress.vision_calls += 1
        call = run_vision_step(self.runtime, self.settings, vision_goal(goal.context, need))
        if self.vision_extends_deadline:
            outcome = await call
            self.deadline += outcome.seconds  # время зрения не съедает бюджет цикла
        else:
            try:
                outcome = await asyncio.wait_for(call, timeout=max(1.0, self.deadline - time.monotonic()))
            except asyncio.TimeoutError:
                outcome = VisionOutcome(False, "зрение не уложилось в бюджет времени")
        after = await self.observe()
        moved = page_changed(obs, after)
        self.progress.mark("navigate", "vision", outcome.ok and moved)
        return after, f"vision «{need[:120]}» → {outcome.note[:200]}; {_outcome(obs, after, moved)}", moved

    async def _log(self, record: dict, obs: Observation, *, error: bool = False) -> None:
        trace = current_trace.get()
        if trace is None:
            return
        trace.log_step(record)
        await trace.snapshot(self.runtime.current_page, error=error, text=obs.text)


def action_error(exc: Exception) -> str:
    """Причина неудачи для истории модели. Из таймаута Playwright достаём, что мешало клику:
    «Timeout 8000ms» модели ничего не говорит, а «перекрыт окном …» — подсказывает закрыть окно и повторить."""
    text = str(exc)
    blocker = re.search(r"(<[^\n]{0,160}?>)[^\n]*? intercepts pointer events", text)
    if blocker:
        return f"элемент перекрыт другим элементом {blocker.group(1)[:160]} — закрой его (окно, баннер) и повтори"
    if "not visible" in text or "element is outside of the viewport" in text:
        return "элемент не виден на экране — прокрути к нему или раскрой блок, где он лежит"
    return text.splitlines()[0][:200]


def _element(obs: Observation, ref: object) -> Element | None:
    try:
        wanted = int(ref)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return next((el for el in obs.elements if el.ref == wanted), None)


def _label(tool: str, decision: dict, element: Element | None) -> str:
    if tool == "fill":
        return f"fill [{decision.get('ref')}] «{str(decision.get('text') or '')[:60]}»" + (" + Enter" if decision.get("submit") else "")
    if tool == "click":
        return f"click [{decision.get('ref')}] «{(element.name if element else '')[:60]}»"
    if tool == "scroll":
        return f"scroll {decision.get('direction') or 'down'}"
    if tool == "goto":
        return f"goto {str(decision.get('url') or '')[:120]}"
    return tool


def _outcome(before: Observation, after: Observation, moved: bool) -> str:
    if not moved:
        return "страница не изменилась"
    if after.url != before.url:
        return f"открыта страница «{after.title[:80]}» {after.url[:160]}"
    return "страница изменилась (тот же адрес)"


def _short_href(href: str, page_host: str) -> str:
    parsed = urlparse(href)
    tail = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    if (parsed.hostname or "") != page_host:
        return href[:120]
    return tail[:120] or "/"


def element_line(el: Element, page_host: str) -> str:
    kind = el.role or el.tag
    if el.tag == "input":
        kind = f"input[{el.input_type or 'text'}]"
    line = f"[{el.ref}] {kind}"
    if el.name:
        line += f" «{el.name[:100]}»"
    if el.placeholder:
        line += f" placeholder=«{el.placeholder[:60]}»"
    if el.value:
        line += f" value=«{el.value[:60]}»"
    if el.hint:
        line += f" ({el.hint})"
    if el.href:
        line += f" → {_short_href(el.href, page_host)}"
    return line


def page_for_model(obs: Observation) -> dict:
    host = urlparse(obs.url).hostname or ""
    scroll = obs.scroll
    position = ""
    if scroll.get("height"):
        position = f"прокручено {scroll.get('y', 0)} из {scroll['height']} px, экран {scroll.get('viewport', 0)} px"
    return {
        "url": obs.url,
        "title": obs.title,
        "scroll": position,
        "text": obs.text[:PAGE_TEXT_FOR_MODEL],
        "text_truncated": len(obs.text) > PAGE_TEXT_FOR_MODEL,
        "elements": [element_line(el, host) for el in _elements_for_model(obs.elements)],
    }


ELEMENTS_FOR_MODEL = 300
ELEMENTS_TAIL = 100  # низ длинной страницы: свежие строки после «Показать ещё» и сама кнопка


def _elements_for_model(elements: list) -> list:
    if len(elements) <= ELEMENTS_FOR_MODEL:
        return elements
    return elements[:ELEMENTS_FOR_MODEL - ELEMENTS_TAIL] + elements[-ELEMENTS_TAIL:]
