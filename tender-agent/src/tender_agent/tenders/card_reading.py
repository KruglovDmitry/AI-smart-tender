"""Карточка закупки, которую прочитала модель: поля, лоты и документы цитатами со страницы.

Принимаем только то, что действительно есть на странице: цитата — в тексте, документ —
среди пронумерованных элементов. Уже известные поля (из выдачи) не затираем.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag

from ..api.schemas import TenderResult
from ..llm.base import LLMClient
from .extraction import parse_date, parse_price
from .models import Element, Observation

CARD_TEXT_FOR_MODEL = 20_000
DETAIL_KEYS = ("procurement_method", "delivery_place", "bid_security", "contract_security", "okpd2")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _element_line(el: Element) -> str:
    line = f"[{el.ref}] {el.role or el.tag} «{el.name[:120]}»"
    if el.href:
        line += f" → {el.href[:200]}"
    if el.download:
        line += " (download)"
    if el.hint:
        line += f" ({el.hint})"
    return line


def card_for_model(obs: Observation) -> dict:
    return {
        "url": obs.url,
        "title": obs.title,
        "text": obs.text[:CARD_TEXT_FOR_MODEL],
        "elements": [_element_line(el) for el in obs.elements],
    }


@dataclass
class CardReading:
    tender: TenderResult
    refs: list[int] = field(default_factory=list)  # документы на этой странице: ref элементов
    filled: list[str] = field(default_factory=list)  # какие поля добавила карточка
    rejected: int = 0  # цитат, которых нет на странице


def accept_card(tender: TenderResult, obs: Observation, data: dict) -> CardReading:
    page = _norm(obs.text)
    updated = tender.model_copy(deep=True)
    reading = CardReading(tender=updated)

    def quote(key: str) -> str | None:
        value = str(data.get(key) or "").strip()
        if not value:
            return None
        if _norm(value) not in page:
            reading.rejected += 1
            return None
        return value

    def fill(name: str, value: object) -> None:
        if value and not getattr(updated, name):
            setattr(updated, name, value)
            updated.sources[name] = "card"
            reading.filled.append(name)

    title = quote("title")
    if title and updated.sources.get("title") == "lot":
        # В выдаче было название лота («№10 Филиал…»), на карточке — предмет всей процедуры.
        updated.title = None
    fill("title", title)
    fill("customer", quote("customer"))
    fill("published_at", parse_date(quote("published")))
    fill("deadline", parse_date(quote("deadline")))
    fill("status", quote("status"))
    price_text = quote("price")
    if price_text and updated.price is None:
        price, currency = parse_price(None, price_text, None)
        fill("price", price)
        fill("currency", currency)
    for key in DETAIL_KEYS:
        value = quote(key)
        if value and not updated.details.get(key):
            updated.details[key] = value[:500]
            updated.sources[key] = "card"
            reading.filled.append(key)
    lots = []
    for item in data.get("lots") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title or _norm(title) not in page:
            continue
        lot = {"title": title[:300]}
        for key in ("number", "price"):
            value = str(item.get(key) or "").strip()
            if value and _norm(value) in page:
                lot[key] = value[:60]
        lots.append(lot)
    if len(lots) > 1:  # один лот — это сама закупка
        updated.details["lots"] = lots
        reading.filled.append("lots")

    by_ref = {el.ref: el for el in obs.elements}
    known = {doc.get("url") for doc in updated.documents}
    for item in data.get("documents") or []:
        try:
            el = by_ref.get(int(item.get("ref") if isinstance(item, dict) else item))
        except (TypeError, ValueError):
            el = None
        if el is None:
            reading.rejected += 1
            continue
        if el.href and urldefrag(el.href)[0] == urldefrag(obs.url)[0]:
            # Ссылка «#» на эту же страницу — не файл: так площадки рисуют закрытый документ
            # (РосТендер: «1 файл» до регистрации). Скачивать нечего.
            reading.rejected += 1
            continue
        name = (str(item.get("name") or "").strip() if isinstance(item, dict) else "") or el.name
        # Кнопка-файл без адреса скачивается кликом; в базе — по имени на странице карточки.
        url = el.href or f"{obs.url}#file:{name[:120]}"
        if url in known:
            continue
        known.add(url)
        updated.documents.append({"name": name[:200], "url": url})
        if el.href:
            updated.document_urls.append(el.href)
        reading.refs.append(el.ref)
    return reading


async def read_card(llm: LLMClient, obs: Observation, *, keyword: str) -> dict:
    return await llm.read_card({"keyword": keyword}, card_for_model(obs))
