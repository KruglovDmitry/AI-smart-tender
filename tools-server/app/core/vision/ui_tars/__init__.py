"""UI-TARS vision backend package (Mode A GROUNDING)."""

from .backend import UiTarsBackend
from .client import UiTarsClient
from .parser import parse_grounding_response, parse_to_css, smart_resize

__all__ = [
    "UiTarsBackend",
    "UiTarsClient",
    "parse_grounding_response",
    "parse_to_css",
    "smart_resize",
]
