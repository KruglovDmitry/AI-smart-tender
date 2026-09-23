"""Qwen-VL grounding/inspect backend via OpenAI-compatible chat API."""

from __future__ import annotations

import logging
import time
from typing import Any

from ... import config
from ..llm.client import chat_completions, extract_json_object, message_text, require_llm
from .base import GroundingCandidate, GroundingResult, InspectionResult
from .scale import (
    clamp_css,
    clamp_xy,
    norm1000_to_image_px,
    png_pixel_size,
    remap_candidates_to_css,
)

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

_INSPECT_PROMPT = """Ты смотришь на screenshot веб-страницы ({width}x{height} px).
Вопрос: {question}

Ответь кратко по сути (1–3 предложения). Без координат, без JSON, без markdown.
Если не уверен — так и скажи.
"""


def _dedupe_near(
    candidates: list[GroundingCandidate],
    *,
    dist_px: int = 8,
) -> list[GroundingCandidate]:
    out: list[GroundingCandidate] = []
    for c in candidates:
        if any(abs(c.x - o.x) < dist_px and abs(c.y - o.y) < dist_px for o in out):
            continue
        out.append(c)
    return out


def _parse_candidates(
    raw: dict[str, Any],
    image_size: tuple[int, int],
) -> list[GroundingCandidate]:
    """
    Parse model JSON into candidates. Clamp to PNG pixel bounds only —
    do NOT clamp to CSS viewport before scale remapping.
    """
    iw, ih = int(image_size[0]), int(image_size[1])
    items: list[GroundingCandidate] = []

    try:
        x = int(raw.get("x") or 0)
        y = int(raw.get("y") or 0)
    except (TypeError, ValueError):
        x, y = 0, 0
    x, y = clamp_xy(x, y, (iw, ih))
    if raw.get("found") or (x or y):
        items.append(GroundingCandidate(x=x, y=y, label="primary"))

    elements = raw.get("elements") if isinstance(raw.get("elements"), list) else []
    for el in elements[:8]:
        if not isinstance(el, dict):
            continue
        try:
            ex = int(el.get("x") or 0)
            ey = int(el.get("y") or 0)
        except (TypeError, ValueError):
            continue
        ex, ey = clamp_xy(ex, ey, (iw, ih))
        label = str(el.get("label") or "")[:120]
        items.append(GroundingCandidate(x=ex, y=ey, label=label))

    return _dedupe_near(items)


def finalize_candidates(
    candidates: list[GroundingCandidate],
    *,
    image_b64: str,
    viewport: tuple[int, int],
    image_size: tuple[int, int] | None = None,
    coords_mode: str | None = None,
) -> list[GroundingCandidate]:
    """
    norm1000→PNG (optional) → remap PNG→CSS → clamp to viewport.
    """
    mode = (coords_mode or getattr(config, "AGENT_VL_COORDS", "pixel") or "pixel").lower()
    img = image_size or png_pixel_size(image_b64) or (int(viewport[0]), int(viewport[1]))

    if mode == "norm1000":
        for c in candidates:
            nx, ny = norm1000_to_image_px(c.x, c.y, img)
            c.x, c.y = clamp_xy(nx, ny, img)

    remap_candidates_to_css(
        candidates,
        image_b64=image_b64,
        viewport=viewport,
        image_size=img,
    )
    for c in candidates:
        c.x, c.y = clamp_css(c.x, c.y, viewport)
    return candidates


class QwenVLBackend:
    name = "qwen_vl"

    async def ground(
        self,
        image_b64: str,
        target: str,
        viewport: tuple[int, int],
    ) -> GroundingResult:
        width, height = int(viewport[0]), int(viewport[1])
        model = config.AGENT_VL_MODEL
        if not image_b64:
            return GroundingResult(
                found=False,
                candidates=[],
                backend=self.name,
                model=model,
                note="empty image",
            )
        try:
            require_llm()
        except RuntimeError as e:
            return GroundingResult(
                found=False,
                candidates=[],
                backend=self.name,
                model=model,
                note=str(e),
            )

        prompt = _LOCATE_PROMPT.format(
            width=width,
            height=height,
            goal=(target or "")[:800],
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
        t0 = time.perf_counter()
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
            logger.warning("QwenVLBackend.ground failed: %s", e)
            return GroundingResult(
                found=False,
                candidates=[],
                backend=self.name,
                model=model,
                note=f"ground error: {e}",
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        img = png_pixel_size(image_b64) or (width, height)
        candidates = _parse_candidates(raw, img)
        finalize_candidates(
            candidates,
            image_b64=image_b64,
            viewport=viewport,
            image_size=img,
        )
        found = bool(raw.get("found")) and bool(candidates)
        if not found and candidates:
            found = True
        return GroundingResult(
            found=found,
            action="click" if found and candidates else "none",
            candidates=candidates,
            backend=self.name,
            model=model,
            note=str(raw.get("note") or ""),
            latency_ms=latency_ms,
            raw=raw if isinstance(raw, dict) else None,
            raw_response=content if isinstance(content, str) else "",
        )

    async def inspect(
        self,
        image_b64: str,
        question: str,
        viewport: tuple[int, int],
    ) -> InspectionResult:
        width, height = int(viewport[0]), int(viewport[1])
        model = config.AGENT_VL_MODEL
        if not image_b64:
            return InspectionResult(
                answer="empty image",
                backend=self.name,
                model=model,
            )
        try:
            require_llm()
        except RuntimeError as e:
            return InspectionResult(answer=str(e), backend=self.name, model=model)

        prompt = _INSPECT_PROMPT.format(
            width=width,
            height=height,
            question=(question or "")[:800],
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
        t0 = time.perf_counter()
        try:
            data = await chat_completions(
                messages,
                model=model,
                tools=None,
                tool_choice=None,
                temperature=0,
                max_tokens=400,
                timeout=90.0,
            )
            answer = message_text((data.get("choices") or [{}])[0].get("message")).strip()
        except Exception as e:
            logger.warning("QwenVLBackend.inspect failed: %s", e)
            answer = f"inspect error: {e}"
        return InspectionResult(
            answer=answer or "(empty)",
            backend=self.name,
            model=model,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
