"""Vision backends — grounding/inspect only; never drive the browser."""

from __future__ import annotations

from ... import config
from .act import click_on_screen, inspect_screen
from .base import (
    GroundingCandidate,
    GroundingResult,
    InspectionResult,
    VisionBackend,
)
from .qwen_vl import QwenVLBackend
from .validator import validate_point

_BACKENDS: dict[str, type] = {
    "qwen_vl": QwenVLBackend,
}


def get_vision_backend(name: str | None = None) -> VisionBackend:
    key = (name or getattr(config, "AGENT_VISION_BACKEND", None) or "qwen_vl").strip().lower()
    cls = _BACKENDS.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown AGENT_VISION_BACKEND={key!r}. Available: {sorted(_BACKENDS)}"
        )
    return cls()


__all__ = [
    "GroundingCandidate",
    "GroundingResult",
    "InspectionResult",
    "VisionBackend",
    "QwenVLBackend",
    "get_vision_backend",
    "validate_point",
    "click_on_screen",
    "inspect_screen",
]
