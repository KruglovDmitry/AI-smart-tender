"""Дочитывание карточек выдачи моделью, когда эвристики дали мусор.

Эвристики `fields.py` хорошо работают на ЕИС и похожей вёрстке, но на части
площадок в «название» попадают заказчик, цена, дата, статус или тип процедуры.
Для таких карточек текст блока отдаётся модели, и она возвращает дословные
цитаты по полям. Значение принимается, только если цитата есть в тексте блока —
тот же приём, что extract в AI-vision.
"""

from __future__ import annotations

import re

from ..heuristics.fields import label_kind
from ..heuristics.titles import suspicious_title
from .models import RawCard

def needs_refine(card: RawCard) -> bool:
    if not card.block_text:
        return False
    return suspicious_title(card.title) or not card.id or not (card.published_at or card.deadline) or not card.price_text


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def apply_reading(cards: list[RawCard], reading: dict) -> int:
    """Применяет ответ модели к карточкам. Возвращает число изменённых полей."""
    changed = 0
    by_index = {index: card for index, card in enumerate(cards)}
    for item in reading.get("cards") or []:
        if not isinstance(item, dict):
            continue
        try:
            card = by_index[int(item.get("block"))]
        except (KeyError, TypeError, ValueError):
            continue
        block = _norm(card.block_text or "")

        def quote(name: str) -> str | None:
            value = item.get(name)
            if not isinstance(value, str) or not value.strip():
                return None
            return value.strip() if _norm(value) in block else None

        title = quote("title")
        if title and suspicious_title(card.title) and not suspicious_title(title):
            card.title = title[:300]
            card.sources["title"] = "llm_block"
            changed += 1
        for model_key, field in (
            ("number", "id"),
            ("customer", "customer"),
            ("published", "published_at"),
            ("deadline", "deadline"),
            ("price", "price_text"),
            ("status", "status"),
        ):
            value = quote(model_key)
            current = getattr(card, field)
            bad_customer = field == "customer" and current and label_kind(current) is not None
            if value and (not current or bad_customer):
                if field == "id":
                    value = re.sub(r"^№\s*", "", value)
                setattr(card, field, value)
                card.sources[field] = "llm_block"
                changed += 1
    return changed


def blocks_for_model(cards: list[RawCard], limit: int = 15) -> tuple[list[RawCard], list[dict]]:
    todo = [card for card in cards if needs_refine(card)][:limit]
    return todo, [{"block": index, "text": (card.block_text or "")[:1500]} for index, card in enumerate(todo)]
