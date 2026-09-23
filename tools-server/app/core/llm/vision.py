"""Narrow VL locate(image, goal) — coordinates only when DOM is empty."""

from __future__ import annotations

import logging
from typing import Any

from ... import config
from .client import chat_completions, extract_json_object, message_text, require_llm

logger = logging.getLogger(__name__)

_LOCATE_PROMPT = """Ты смотришь на screenshot веб-страницы.
Viewport: {width}x{height} px (0,0 = левый верхний угол).

Цель: {goal}

Найди ОДИН элемент, по которому нужно кликнуть/действовать для этой цели.
Верни ТОЛЬКО JSON без markdown:
{{
  "found": true/false,
  "x": 0,
  "y": 0,
  "elements": [{{"label": "...", "x": 0, "y": 0}}],
  "note": "кратко"
}}

Правила:
- x,y — пиксели viewport, центр кликабельной области.
- Если цель не видна — found=false, x=0, y=0, note с причиной.
- Не описывай страницу целиком — только ответ под цель.
"""


async def locate(
    image_b64: str,
    goal: str,
    viewport: tuple[int, int],
) -> dict[str, Any]:
    """
    Call AGENT_VL_MODEL with a screenshot. Returns
    {found, x, y, elements, note} — use ONLY when DOM snapshot is empty/insufficient.
    """
    width, height = int(viewport[0]), int(viewport[1])
    if not image_b64:
        return {
            "found": False,
            "x": 0,
            "y": 0,
            "elements": [],
            "note": "empty image",
        }

    try:
        require_llm()
    except RuntimeError as e:
        return {
            "found": False,
            "x": 0,
            "y": 0,
            "elements": [],
            "note": str(e),
        }

    model = config.AGENT_VL_MODEL
    prompt = _LOCATE_PROMPT.format(
        width=width,
        height=height,
        goal=(goal or "")[:800],
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                },
            ],
        }
    ]
    try:
        data = await chat_completions(
            messages,
            model=model,
            tools=None,
            tool_choice=None,
            temperature=0,
            max_tokens=600,
            timeout=120.0,
        )
        content = message_text((data.get("choices") or [{}])[0].get("message"))
        raw = extract_json_object(content)
    except Exception as e:
        logger.warning("vision.locate failed: %s", e)
        return {
            "found": False,
            "x": 0,
            "y": 0,
            "elements": [],
            "note": f"locate error: {e}",
        }

    found = bool(raw.get("found"))
    try:
        x = int(raw.get("x") or 0)
        y = int(raw.get("y") or 0)
    except (TypeError, ValueError):
        x, y = 0, 0
        found = False

    # clamp to viewport
    x = max(0, min(x, max(0, width - 1)))
    y = max(0, min(y, max(0, height - 1)))
    elements = raw.get("elements") if isinstance(raw.get("elements"), list) else []
    note = str(raw.get("note") or "")

    return {
        "found": found,
        "x": x,
        "y": y,
        "elements": elements[:8],
        "note": note,
    }
