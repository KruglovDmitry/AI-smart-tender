from __future__ import annotations

from ..tenders.models import Observation


def page_changed(before: Observation, after: Observation) -> bool:
    return before.url != after.url or before.signature != after.signature


def field_has_value(after: Observation, ref: int | None, value: str) -> bool:
    if ref is None:
        return False
    for el in after.elements:
        if el.ref == ref and (el.value or "") == value:
            return True
    return (after.search_value or "") == value
