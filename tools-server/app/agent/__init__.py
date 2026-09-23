"""High-level platform agent package."""

from .loop import run_platform_task
from .prompt import SYSTEM_PROMPT_BROWSER, SYSTEM_PROMPT_PLATFORM

__all__ = [
    "SYSTEM_PROMPT_BROWSER",
    "SYSTEM_PROMPT_PLATFORM",
    "run_platform_task",
]
