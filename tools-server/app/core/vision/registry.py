"""Vision backend factories (grounding + perception)."""

from __future__ import annotations

from ... import config
from .base import VisionBackend
from .qwen_vl import QwenVLBackend
from .ui_tars import UiTarsBackend

_BACKENDS: dict[str, type] = {
    "qwen_vl": QwenVLBackend,
    "ui_tars": UiTarsBackend,
}


def get_vision_backend(name: str | None = None) -> VisionBackend:
    """Grounding backend (where is X?). Default: ui_tars."""
    key = (name or getattr(config, "AGENT_VISION_BACKEND", None) or "ui_tars").strip().lower()
    cls = _BACKENDS.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown AGENT_VISION_BACKEND={key!r}. Available: {sorted(_BACKENDS)}"
        )
    return cls()


def get_perception_backend(name: str | None = None) -> VisionBackend:
    """Perception backend (what is on screen?). Default: qwen_vl."""
    key = (
        name or getattr(config, "AGENT_PERCEPTION_BACKEND", None) or "qwen_vl"
    ).strip().lower()
    if key == "ui_tars":
        if not getattr(config, "UI_TARS_BASE_URL", ""):
            raise RuntimeError("perception ui_tars: UI_TARS_BASE_URL is not set")
        return UiTarsBackend()
    if key == "qwen_vl":
        if not config.perception_configured():
            raise RuntimeError(
                "perception qwen_vl: set AGENT_PERCEPTION_BASE_URL and AGENT_PERCEPTION_API_KEY"
            )
        return QwenVLBackend()
    raise ValueError(
        f"Unknown AGENT_PERCEPTION_BACKEND={key!r}. Available: qwen_vl | ui_tars"
    )
