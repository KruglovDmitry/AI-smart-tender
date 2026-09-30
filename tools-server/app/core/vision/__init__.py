"""Vision backends — grounding / perception; never drive the browser."""

from __future__ import annotations

from .act import (
    click_on_screen,
    click_target,
    ground_validated,
    inspect_screen,
    scroll_screen,
    type_into_target,
)
from .base import (
    GroundingCandidate,
    GroundingResult,
    InspectionResult,
    ScreenTarget,
    VisionBackend,
)
from .qwen_vl import QwenVLBackend
from .registry import get_perception_backend, get_vision_backend
from .ui_tars import UiTarsBackend
from .validators import validate_point

__all__ = [
    "GroundingCandidate",
    "GroundingResult",
    "InspectionResult",
    "ScreenTarget",
    "VisionBackend",
    "QwenVLBackend",
    "UiTarsBackend",
    "get_vision_backend",
    "get_perception_backend",
    "validate_point",
    "click_on_screen",
    "click_target",
    "ground_validated",
    "inspect_screen",
    "scroll_screen",
    "type_into_target",
]
