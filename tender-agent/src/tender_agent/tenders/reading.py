"""Записи выдачи, которые прочитала модель: принимаем только то, что действительно есть на странице."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..api.schemas import TenderResult
from ..llm.base import LLMClient
from .extraction import card_from_raw, parse_date
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
    numbers: list[str] = field(default_factory=list)  # номера всех записей, что назвала модель
    # Даты публикации всех записей страницы, и отклонённых тоже: по ним видно, отсортирована ли выдача
    # по дате, даже когда подошли одна-две записи (Сбер-АСТ: 2 активных из 20).
    published: list[str] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


def links_for_model(obs: Observation) -> list[dict]:
    """Ссылки страницы и кликабельные элементы без адреса (строки таблиц, открывающие карточку скриптом)."""
    return [{"ref": el.ref, "text": el.name[:120], "href": (el.href or "")[:200]}
            for el in obs.elements if el.href or el.role == "clickable"][:MAX_LINKS]


# Этапы, на которых заявку уже не подать.
_CLOSED = re.compile(r"заверш|отмен|не\s*состоял|состоял|архив|закрыт(?:а|о|ы)?(?![а-яё])|работа комисси|рассмотрени|подведени|"
                     r"итог|исполнени|заключ|приостанов|прием заявок окончен|приём заявок окончен", re.I)


def accept_records(obs: Observation, data: dict, *, keyword: str, sort_required: bool,
                   active_only: bool = False, today: str | None = None) -> ResultsReading:
    """Цитаты сверяются с текстом страницы, ссылка — со ссылками страницы."""
    page = _norm(obs.text)
    by_ref = {el.ref: el for el in obs.elements}
    reading = ResultsReading()
    for item in data.get("records") or []:
        if not isinstance(item, dict):
            continue
        reading.found += 1
        if item.get("number"):
            reading.numbers.append(str(item["number"]).strip())
        published = parse_date(str(item.get("published") or ""))
        if published:
            reading.published.append(published)
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
        if item.get("relevant") is False:
            # Поиск площадки нашёл слово где-то в документах или позициях (ЭТП ГПБ), а закупают другое.
            reading.reject("не по теме запроса")
            continue
        if active_only and _closed(card, today or date.today().isoformat()):
            reading.reject("приём заявок закончен")
            continue
        if lot:
            # Строка выдачи — лот процедуры: склеится с другими лотами по номеру процедуры (ranking),
            # а название процедуры возьмём с её карточки.
            card.details["lot"] = lot[:40]
        if element.href:
            reading.accepted.append(card)
        else:
            reading.pending.append((card, element))
    _merge_dash_lots(reading)
    return reading


def _closed(card: TenderResult, today: str) -> bool:
    if card.deadline and card.deadline < today:
        return True
    return bool(card.status and _CLOSED.search(card.status))


_DASH_LOT = re.compile(r"^(.{4,}?)-(\d{1,3})$")


def _merge_dash_lots(reading: ResultsReading) -> None:
    """Лоты вида «5582689-1», «5582689-2» (Фабрикант) — одна процедура, если в выдаче есть несколько таких.
    Один номер с хвостом не трогаем: «1203219-10829500001126006716» — самостоятельный номер."""
    cards = reading.accepted + [card for card, _ in reading.pending]
    bases: dict[str, int] = {}
    for card in cards:
        found = _DASH_LOT.match(card.id or "")
        if found:
            bases[found.group(1)] = bases.get(found.group(1), 0) + 1
    for card in cards:
        found = _DASH_LOT.match(card.id or "")
        if found and bases[found.group(1)] > 1 and "lot" not in card.details:
            card.id = found.group(1)
            card.details["lot"] = found.group(2)


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


async def read_results(llm: LLMClient, obs: Observation, *, keyword: str, sort_required: bool,
                       active_only: bool = False, skip: list[str] | None = None) -> ResultsReading:
    # skip — номера уже прочитанных записей: после «Показать ещё» страница растёт (10 → 40 записей),
    # и без него модель каждый раз переписывает всю выдачу заново (до двух минут на ответ).
    data = await llm.read_results(
        {"keyword": keyword, "skip": (skip or [])[-300:]},
        {"url": obs.url, "title": obs.title, "text": obs.text[:RESULTS_TEXT_FOR_MODEL], "links": links_for_model(obs)},
    )
    return accept_records(obs, data, keyword=keyword, sort_required=sort_required, active_only=active_only)
