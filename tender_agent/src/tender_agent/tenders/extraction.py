from __future__ import annotations

import re
from datetime import datetime

from ..api.schemas import TenderResult
from ..sites.generic import FILE_RE
from .models import Element, Observation, RawCard

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


def _page_blob(obs: Observation) -> str:
    parts = [obs.title, obs.text_excerpt, *obs.headings]
    for element in obs.elements:
        parts.extend([element.name, element.label, element.href or ""])
    return " ".join(part for part in parts if part)


def _text(value: object) -> str:
    return str(value or "").strip()


def _on_page(value: str, blob: str) -> bool:
    return bool(value) and value.casefold() in blob.casefold()


def _date_on_page(value: str, blob: str) -> str | None:
    parsed = parse_date(value)
    if parsed is None:
        return None
    year, month, day = parsed.split("-")
    dotted = f"{day}.{month}.{year}"
    if _on_page(value, blob) or parsed in blob or dotted in blob:
        return parsed
    return None


def _is_document(element: Element) -> bool:
    href = element.href or ""
    name = element.name or ""
    if element.download or FILE_RE.search(href) or FILE_RE.search(name):
        return True
    return bool(re.search(r"документ|скачать|файл|вложен", f"{name} {href}", re.I))


def _element(obs: Observation, ref: int) -> Element | None:
    for element in obs.elements:
        if element.ref == ref:
            return element
    return None


def apply_page_reading(tender: TenderResult, obs: Observation, data: dict | None) -> tuple[TenderResult, list[int]]:
    """Дополняет карточку только теми значениями, которые есть на открытой странице."""
    updated = tender.model_copy(deep=True)
    blob = _page_blob(obs)
    payload = data or {}
    customer = _text(payload.get("customer"))
    if customer and not updated.customer and _on_page(customer, blob):
        updated.customer = customer
        updated.sources["customer"] = "llm"
    deadline = _date_on_page(_text(payload.get("deadline")), blob)
    published = _date_on_page(_text(payload.get("published_at")), blob)
    low = blob.casefold()
    if published and published == deadline and "публикац" not in low and "размещен" not in low:
        published = None
    if deadline and not updated.deadline:
        updated.deadline = deadline
        updated.sources["deadline"] = "llm"
    if published and not updated.published_at:
        updated.published_at = published
        updated.sources["published_at"] = "llm"
        if updated.matched_keyword and updated.verification_status == "needs_review":
            updated.verification_status = "verified"
    price_text = _text(payload.get("price_text"))
    if price_text and updated.price is None and _on_page(price_text, blob):
        price, currency = parse_price(None, price_text, None)
        if price is not None:
            updated.price = price
            updated.currency = updated.currency or currency
            updated.sources["price"] = "llm"
    status = _text(payload.get("status"))
    if status and not updated.status and _on_page(status, blob):
        updated.status = status
        updated.sources["status"] = "llm"
    known = {element.ref for element in obs.elements}
    refs: list[int] = []
    for item in payload.get("document_refs") or []:
        try:
            ref = int(item)
        except (TypeError, ValueError):
            continue
        element = _element(obs, ref) if ref in known else None
        if element and element.ref not in refs and _is_document(element):
            refs.append(element.ref)
    urls: list[str] = []
    for ref in [*refs, *obs.file_refs]:
        element = _element(obs, ref)
        href = (element.href if element else "") or ""
        if href and href not in urls:
            urls.append(href)
    updated.document_urls = urls
    return updated, refs or list(obs.file_refs)
