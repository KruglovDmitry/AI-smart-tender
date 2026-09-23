"""Shim: locate() kept for adapters; prefer core.vision.get_vision_backend()."""

from __future__ import annotations

from typing import Any


async def locate(
    image_b64: str,
    goal: str,
    viewport: tuple[int, int],
) -> dict[str, Any]:
    """Backward-compatible dict for platform adapters (Rosatom SPA fallback)."""
    from ..vision import get_vision_backend

    backend = get_vision_backend()
    result = await backend.ground(image_b64, goal, viewport)
    primary = result.candidates[0] if result.candidates else None
    return {
        "found": result.found,
        "x": primary.x if primary else 0,
        "y": primary.y if primary else 0,
        "elements": [
            {"label": c.label, "x": c.x, "y": c.y} for c in result.candidates
        ],
        "note": result.note,
        "backend": result.backend,
        "model": result.model,
        "latency_ms": result.latency_ms,
    }
