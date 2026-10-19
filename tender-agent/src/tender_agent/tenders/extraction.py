from __future__ import annotations

import re
from datetime import datetime

from ..api.schemas import TenderResult
from .models import RawCard

_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})")
_DOT = re.compile(r"(\d{2}\.\d{2}\.(?:\d{4}|\d{2}))(?!\d)")
# «16 окт. 2026», «3 октября 2026» (РТС-тендер и др.).
_WORDS = re.compile(r"(\d{1,2})\s+(янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек)[а-я]*\.?\s+(\d{4})", re.I)
_MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6, "июл": 7, "авг": 8,
           "сен": 9, "окт": 10, "ноя": 11, "дек": 12}


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
            fmt = "%d.%m.%Y" if len(dotted.group(1)) == 10 else "%d.%m.%y"
            return datetime.strptime(dotted.group(1), fmt).date().isoformat()
        except ValueError:
            return None
    worded = _WORDS.search(text)
    if worded:
        try:
            month = _MONTHS[worded.group(2).casefold()]
            return datetime(int(worded.group(3)), month, int(worded.group(1))).date().isoformat()
        except (KeyError, ValueError):
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
        return float(re.sub(r"\s", "", raw).replace(",", ".")), cur
    except ValueError:
        return None, cur


def card_from_raw(raw: RawCard, *, keyword: str, sort_required: bool) -> TenderResult | None:
    """Запись выдачи → результат. Слово запроса в названии — verified, иначе needs_review."""
    title = (raw.title or "").strip() or None
    url = (raw.href or "").strip() or None
    if not title or not url:
        return None
    published = parse_date(raw.published_at)
    deadline = parse_date(raw.deadline)
    price, currency = parse_price(raw.amount, raw.price_text, raw.currency)
    matched = bool(keyword) and keyword.casefold() in title.casefold()
    status = "verified" if matched else "needs_review"
    if sort_required and published is None:
        status = "needs_review"
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
        sources=dict(raw.sources),
    )
