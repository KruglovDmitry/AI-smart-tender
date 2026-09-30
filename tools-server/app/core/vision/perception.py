"""Shared perception prompts and JSON parsers (list_targets / answer)."""

from __future__ import annotations

import json
import re
from typing import Any

from .base import ScreenTarget

LIST_TARGETS_PROMPT = """Перечисли все видимые элементы интерфейса, на которые можно нажать или в которые можно ввести текст.
Для каждого — точная подпись как на экране; для иконок — краткое описание («иконка скрепки у строки 2»).
Верни ТОЛЬКО JSON-массив объектов {{"label": "...", "kind": "button|link|tab|input|icon|checkbox|select|other"}}.
Не больше 40. Без markdown, без координат, без пояснений вне JSON.
Viewport: {width}x{height} px.
"""

_KIND_OK = {
    "button",
    "link",
    "tab",
    "input",
    "icon",
    "checkbox",
    "select",
    "other",
}


def parse_targets_payload(text: str) -> list[ScreenTarget]:
    """Parse model JSON array (or {targets:[...]}) into ScreenTarget list."""
    raw = (text or "").strip()
    if not raw:
        return []
    data: Any = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = None
        if data is None:
            m2 = re.search(r"\{[\s\S]*\}", raw)
            if m2:
                try:
                    data = json.loads(m2.group(0))
                except json.JSONDecodeError:
                    data = None
    items: list[Any]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = list(data.get("targets") or data.get("elements") or [])
    else:
        items = []
    out: list[ScreenTarget] = []
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label") or it.get("text") or it.get("name") or "").strip()
        if not label:
            continue
        label = re.sub(r"\s+", " ", label)[:80]
        kind = str(it.get("kind") or "other").strip().lower()
        if kind not in _KIND_OK:
            kind = "other"
        key = f"{kind}|{label.lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(ScreenTarget(label=label, kind=kind))
        if len(out) >= 40:
            break
    return out
