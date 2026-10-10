from __future__ import annotations

import re
from datetime import datetime

from ..api.schemas import TenderResult
from .models import RawCard

_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})")
_DOT = re.compile(r"(\d{2}\.\d{2}\.\d{4})")


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    iso = _ISO.search(text)
    if iso:
        return iso.group(1)
    dotted = _DOT.search(text)
    if dotted:
        try:
            return datetime.strptime(dotted.group(1), "%d.%m.%Y").date().isoformat()
        except ValueError:
            return None
    return None


def parse_price(amount: str | None, text: str | None, currency: str | None) -> tuple[float | None, str | None]:
    cur = (currency or "").strip() or None
    blob = text or ""
    low = blob.casefold()
    if "₽" in blob or "руб" in low:
        cur = cur or "RUB"
    elif "₸" in blob or "тенге" in low:
        cur = cur or "KZT"
    elif "$" in blob:
        cur = cur or "USD"
    raw = (amount or "").strip()
    if not raw:
        match = re.search(r"(\d[\d\s]*([.,]\d+)?)", blob)
        raw = match.group(1) if match else ""
    if not raw:
        return None, cur
    try:
        return float(raw.replace(" ", "").replace(",", ".")), cur
    except ValueError:
        return None, cur


def _matched(title: str | None, keyword: str) -> bool:
    return bool(keyword) and keyword.casefold() in (title or "").casefold()


def card_from_raw(raw: RawCard, *, keyword: str, keyword_applied: bool, sort_required: bool) -> TenderResult | None:
    title = (raw.title or "").strip() or None
    url = (raw.href or "").strip() or None
    if not title or not url:
        return None
    published = parse_date(raw.published_at)
    deadline = parse_date(raw.deadline)
    price, currency = parse_price(raw.amount, raw.price_text, raw.currency)
    matched = _matched(title, keyword)
    if matched:
        status = "verified"
    elif keyword_applied:
        status = "needs_review"
    else:
        return None
    if sort_required and published is None and status == "verified":
        status = "needs_review"
    sources = dict(raw.sources)
    if published:
        sources.setdefault("published_at", "page")
    if raw.deadline and not raw.published_at:
        sources["deadline_not_used_as_published"] = "true"
    return TenderResult(
        id=(raw.id or "").strip() or None,
        title=title,
        url=url,
        customer=(raw.customer or "").strip() or None,
        published_at=published,
        deadline=deadline,
        price=price,
        currency=currency,
        status=(raw.status or "").strip() or None,
        matched_keyword=keyword if matched else None,
        verification_status=status,  # type: ignore[arg-type]
        sources=sources,
    )
