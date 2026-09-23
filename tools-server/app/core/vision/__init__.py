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
from .ui_tars import UiTarsBackend
from .validators import validate_point

_BACKENDS: dict[str, type] = {
    "qwen_vl": QwenVLBackend,
    "ui_tars": UiTarsBackend,
}


def get_vision_backend(name: str | None = None) -> VisionBackend:
    key = (name or getattr(config, "AGENT_VISION_BACKEND", None) or "ui_tars").strip().lower()
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
    "UiTarsBackend",
    "get_vision_backend",
    "validate_point",
    "click_on_screen",
    "inspect_screen",
]
