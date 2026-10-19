from __future__ import annotations

from ..api.schemas import TenderResult


def _lot(card: TenderResult) -> dict:
    lot = {"title": card.title, "url": card.url, "price": card.price}
    if card.details.get("lot"):
        lot["number"] = card.details["lot"]
    return lot


def rank_tenders(
    cards: list[TenderResult],
    *,
    sort_by: str | None,
    limit: int,
) -> list[TenderResult]:
    kept: list[TenderResult] = []
    by_key: dict[str, TenderResult] = {}
    for card in cards:
        if card.verification_status == "incomplete" or not (card.url or "").strip():
            continue
        # Закупка — это процедура: строки выдачи с одним номером (лоты на Росэлторге и т.п.)
        # склеиваются в одну запись, лоты сохраняются в details.lots.
        key = (card.id or "").strip() or (card.url or "").strip()
        first = by_key.get(key)
        if first is None:
            by_key[key] = card
            kept.append(card)
            continue
        if (card.url or "") != (first.url or "") and card.id:
            lots = first.details.setdefault("lots", [_lot(first)])
            if all(lot.get("url") != card.url for lot in lots):
                lots.append(_lot(card))
    if sort_by == "published_at":
        dated = [card for card in kept if card.published_at]
        undated = [card for card in kept if not card.published_at]
        dated.sort(key=lambda card: card.published_at or "", reverse=True)
        kept = dated + undated
    return kept[: max(0, limit)]
