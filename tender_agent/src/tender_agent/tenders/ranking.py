from __future__ import annotations

from ..api.schemas import TenderResult


def rank_tenders(
    cards: list[TenderResult],
    *,
    sort_by: str | None,
    limit: int,
) -> list[TenderResult]:
    kept: list[TenderResult] = []
    seen: set[tuple[str, str]] = set()
    for card in cards:
        if card.verification_status == "incomplete":
            continue
        key = ((card.id or "").strip(), (card.url or "").strip())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        kept.append(card)
    if sort_by == "published_at":
        dated = [card for card in kept if card.published_at]
        undated = [card for card in kept if not card.published_at]
        dated.sort(key=lambda card: card.published_at or "", reverse=True)
        kept = dated + undated
    return kept[: max(0, limit)]
