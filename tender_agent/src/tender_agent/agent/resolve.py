"""Адрес карточки для записи выдачи без ссылки: кликнуть строку, запомнить адрес, вернуться.

Так устроены выдачи, где строка таблицы открывает карточку скриптом (Росатом: ``<tr>`` без ссылок,
клик ведёт на ``?link=procurements&obj_id=…``). После возврата выдача снимается заново, а строка
ищется по тексту: номера элементов после перерисовки другие. Если выдача после возврата сбросилась
(строки не нашлось), остальные записи пропускаются — об этом пишется в историю модели.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from ..api.schemas import TenderResult
from ..browser.snapshot import get_page_state
from ..tenders.models import Element, Observation
from . import actions


def _same_row(obs: Observation, wanted: Element) -> Element | None:
    name = wanted.name.strip()
    return next((el for el in obs.elements if not el.href and el.name.strip() == name), None)


async def resolve_links(runtime, obs: Observation, pending: list[tuple[TenderResult, Element]],
                        need: int, host: str,
                        restore: Callable[[], Awaitable[bool]] | None = None) -> tuple[list[TenderResult], str]:
    """Вернуть записи с адресами карточек (не больше need) и строку для истории модели.

    restore — повторить последний поиск, если сайт сбросил выдачу после возврата из карточки
    (Росатом: выдача по запросу не живёт в адресе, после «назад» показывается общий список)."""
    page = runtime.current_page
    list_url = page.url
    resolved: list[TenderResult] = []
    stopped = ""
    restored = 0
    current = obs
    for card, element in pending[:need]:
        row = _same_row(current, element)
        if row is None and restore is not None and await restore():
            restored += 1
            await actions.settle(runtime)
            current = await get_page_state(runtime)
            row = _same_row(current, element)
        if row is None:
            stopped = "выдача после возврата изменилась — строку не нашёл"
            break
        try:
            await actions.click(runtime, row.ref, host)
            for _ in range(32):  # до 8 с: адрес меняет скрипт
                if page.url != list_url:
                    break
                await page.wait_for_timeout(250)
        except Exception as exc:
            stopped = f"клик по строке не удался: {str(exc).splitlines()[0][:120]}"
            break
        if page.url == list_url:
            stopped = "клик по строке не открыл карточку"
            break
        card.url = page.url
        resolved.append(card)
        await actions.back(runtime)
        if page.url != list_url:
            await actions.goto(runtime, list_url, runtime.settings, host)
            await actions.settle(runtime)
        current = await get_page_state(runtime)
    note = f"адреса карточек узнал кликом по строкам: {len(resolved)}"
    if restored:
        note += f" (выдача сбрасывалась после возврата, поиск повторён {restored} раз)"
    if stopped:
        note += f"; остановился: {stopped}"
    return resolved, note
