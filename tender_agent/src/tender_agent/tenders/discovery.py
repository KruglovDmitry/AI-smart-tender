from __future__ import annotations

from urllib.parse import urlparse

from ..sites.generic import CARD_HREF_RE, FILE_RE, NEXT_RE, SEARCH_RE, SECTION_RE, SORT_RE, SUBMIT_RE
from .models import Element, Observation, RawCard


def _blob(el: Element) -> str:
    return " ".join(
        part for part in (el.name, el.placeholder, el.label, el.role) if part
    )


def pick_search(elements: list[Element], preferred: str | None = None) -> Element | None:
    best: Element | None = None
    best_score = 0
    preferred_l = (preferred or "").strip().lower()
    for el in elements:
        if el.input_type in {"hidden", "password", "checkbox", "submit", "button", "radio", "file"}:
            continue
        if el.tag not in {"input", "textarea"} and el.role not in {"textbox", "searchbox", "combobox"}:
            continue
        score = 0
        blob = _blob(el)
        if SEARCH_RE.search(blob):
            score += 5
        if el.role == "searchbox" or el.input_type == "search":
            score += 3
        if preferred_l and preferred_l in blob.lower():
            score += 6
        if score == 0 and (el.name or "").strip().lower() == "search":
            score = 1
        if score > best_score:
            best = el
            best_score = score
    return best


def pick_submit(elements: list[Element], search: Element | None) -> Element | None:
    del search
    for el in elements:
        if el.tag not in {"button", "input"} and el.role != "button":
            continue
        if el.input_type in {"text", "search", "password"}:
            continue
        if SUBMIT_RE.search(_blob(el)) or el.input_type == "submit":
            return el
    return None


def pick_sort(elements: list[Element]) -> Element | None:
    for el in elements:
        if SORT_RE.search(_blob(el)):
            return el
    return None


def pick_next(elements: list[Element]) -> Element | None:
    for el in elements:
        if (el.rel or "").lower() == "next":
            return el
    for el in elements:
        if NEXT_RE.search((el.name or "").strip()):
            return el
    return None


def pick_section(elements: list[Element]) -> Element | None:
    for el in elements:
        if not el.href:
            continue
        path = urlparse(el.href).path or ""
        if SECTION_RE.search(_blob(el)) or SECTION_RE.search(path):
            return el
    return None


def file_refs(elements: list[Element]) -> list[int]:
    found: list[int] = []
    for el in elements:
        href = el.href or ""
        if el.download or FILE_RE.search(href) or FILE_RE.search(el.name or ""):
            found.append(el.ref)
    return found


def fallback_cards(elements: list[Element]) -> list[RawCard]:
    """Ссылки на карточки, если на странице нет размеченной выдачи."""
    found: list[RawCard] = []
    seen: set[str] = set()
    for el in elements:
        href = (el.href or "").strip()
        title = (el.name or "").strip()
        if len(title) < 8 or not href or href in seen:
            continue
        if el.download or FILE_RE.search(href):
            continue
        path = urlparse(href).path or ""
        if not (CARD_HREF_RE.search(path) or CARD_HREF_RE.search(title)):
            continue
        seen.add(href)
        found.append(RawCard(title=title, href=href, sources={"title": "link"}))
        if len(found) >= 30:
            break
    return found


def bind_controls(obs: Observation, preferred_placeholder: str | None = None) -> Observation:
    search = pick_search(obs.elements, preferred_placeholder)
    obs.search_ref = search.ref if search else None
    obs.search_value = (search.value or "") if search else ""
    submit = pick_submit(obs.elements, search)
    obs.submit_ref = submit.ref if submit else None
    sort = pick_sort(obs.elements)
    obs.sort_ref = sort.ref if sort else None
    nxt = pick_next(obs.elements)
    obs.next_ref = nxt.ref if nxt else None
    obs.file_refs = file_refs(obs.elements)
    return obs


def find_elements(obs: Observation, query: str) -> list[Element]:
    needle = query.casefold()
    return [el for el in obs.elements if needle in _blob(el).casefold() or needle in (el.href or "").casefold()]


def inspect_element(obs: Observation, ref: int | None) -> Element | None:
    for el in obs.elements:
        if el.ref == ref:
            return el
    return None
