"""Записи выдачи, которые прочитала модель: принимаем только то, что действительно есть на странице."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..api.schemas import TenderResult
from ..llm.base import LLMClient
from .extraction import card_from_raw
from .models import Element, Observation, RawCard

RESULTS_TEXT_FOR_MODEL = 20_000
MAX_LINKS = 300


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


@dataclass
class ResultsReading:
    found: int = 0  # сколько записей назвала модель
    accepted: list[TenderResult] = field(default_factory=list)
    # Записи, у которых вместо ссылки кликабельный элемент (строка таблицы со скриптом, Росатом):
    # (запись без адреса, элемент). Адрес карточки узнаётся кликом — agent/resolve.py.
    pending: list[tuple[TenderResult, Element]] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)  # причина → сколько

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


def links_for_model(obs: Observation) -> list[dict]:
    """Ссылки страницы и кликабельные элементы без адреса (строки таблиц, открывающие карточку скриптом)."""
    return [{"ref": el.ref, "text": el.name[:120], "href": (el.href or "")[:200]}
            for el in obs.elements if el.href or el.role == "clickable"][:MAX_LINKS]


def accept_records(obs: Observation, data: dict, *, keyword: str, sort_required: bool) -> ResultsReading:
    """Цитаты сверяются с текстом страницы, ссылка — со ссылками страницы."""
    page = _norm(obs.text)
    by_ref = {el.ref: el for el in obs.elements}
    reading = ResultsReading()
    for item in data.get("records") or []:
        if not isinstance(item, dict):
            continue
        reading.found += 1
        title = str(item.get("title") or "").strip()
        if not title or _norm(title) not in page:
            reading.reject("название не найдено на странице")
            continue
        try:
            element = by_ref.get(int(item.get("link_ref")))
        except (TypeError, ValueError):
            element = None
        if element is None or not (element.href or element.role == "clickable"):
            reading.reject("нет ссылки на карточку")
            continue

        def quote(key: str) -> str | None:
            value = str(item.get(key) or "").strip()
            return value if value and _norm(value) in page else None

        number, lot = split_lot(quote("number"))
        lot = str(item.get("lot") or "").strip() or lot
        # Без адреса временно ставим адрес выдачи: настоящий узнаем кликом по элементу.
        raw = RawCard(id=number, title=title, href=element.href or obs.url, customer=quote("customer"),
                      published_at=quote("published"), deadline=quote("deadline"), price_text=quote("price"),
                      status=quote("status"), sources={"title": "lot" if lot else "llm"})
        card = card_from_raw(raw, keyword=keyword, sort_required=sort_required)
        if card is None:
            continue
        if lot:
            # Строка выдачи — лот процедуры: склеится с другими лотами по номеру процедуры (ranking),
            # а название процедуры возьмём с её карточки.
            card.details["lot"] = lot[:40]
        if element.href:
            reading.accepted.append(card)
        else:
            reading.pending.append((card, element))
    return reading


_LOT = re.compile(r"[\s,;]*\(?\s*лот\s*№?\s*(\d+)\s*\)?\s*$", re.I)


def split_lot(number: str | None) -> tuple[str | None, str]:
    """«№ B0510261309243 (Лот 6)» → («B0510261309243», «6»). Номер процедуры — ключ закупки в базе."""
    if not number:
        return None, ""
    number = re.sub(r"^(?:№|N|No\.?)\s*", "", number.strip(), flags=re.I)
    lot = _LOT.search(number)
    if lot:
        number = number[: lot.start()]
    return number.strip() or None, lot.group(1) if lot else ""


async def read_results(llm: LLMClient, obs: Observation, *, keyword: str, sort_required: bool) -> ResultsReading:
    data = await llm.read_results(
        {"keyword": keyword},
        {"url": obs.url, "title": obs.title, "text": obs.text[:RESULTS_TEXT_FOR_MODEL], "links": links_for_model(obs)},
    )
    return accept_records(obs, data, keyword=keyword, sort_required=sort_required)
