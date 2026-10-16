"""Записи выдачи, которые прочитала модель: принимаем только то, что действительно есть на странице."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..api.schemas import TenderResult
from ..llm.base import LLMClient
from .extraction import card_from_raw
from .models import Observation, RawCard

RESULTS_TEXT_FOR_MODEL = 20_000
MAX_LINKS = 300


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


@dataclass
class ResultsReading:
    found: int = 0  # сколько записей назвала модель
    accepted: list[TenderResult] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)  # причина → сколько

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


def links_for_model(obs: Observation) -> list[dict]:
    return [{"ref": el.ref, "text": el.name[:120], "href": (el.href or "")[:200]}
            for el in obs.elements if el.href][:MAX_LINKS]


def accept_records(obs: Observation, data: dict, *, keyword: str, sort_required: bool) -> ResultsReading:
    """Цитаты сверяются с текстом страницы, ссылка — со ссылками страницы."""
    page = _norm(obs.text)
    hrefs = {el.ref: el.href for el in obs.elements if el.href}
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
            href = hrefs.get(int(item.get("link_ref")))
        except (TypeError, ValueError):
            href = None
        if not href:
            reading.reject("нет ссылки на карточку")
            continue

        def quote(key: str) -> str | None:
            value = str(item.get(key) or "").strip()
            return value if value and _norm(value) in page else None

        number = quote("number")
        if number:
            # «№ 32616477643» → «32616477643»: номер — ключ закупки в базе.
            number = re.sub(r"^(?:№|N|No\.?)\s*", "", number, flags=re.I).strip() or None
        raw = RawCard(id=number, title=title, href=href, customer=quote("customer"),
                      published_at=quote("published"), deadline=quote("deadline"), price_text=quote("price"),
                      status=quote("status"), sources={"title": "llm"})
        card = card_from_raw(raw, keyword=keyword, sort_required=sort_required)
        if card is not None:
            reading.accepted.append(card)
    return reading


async def read_results(llm: LLMClient, obs: Observation, *, keyword: str, sort_required: bool) -> ResultsReading:
    data = await llm.read_results(
        {"keyword": keyword},
        {"url": obs.url, "title": obs.title, "text": obs.text[:RESULTS_TEXT_FOR_MODEL], "links": links_for_model(obs)},
    )
    return accept_records(obs, data, keyword=keyword, sort_required=sort_required)
