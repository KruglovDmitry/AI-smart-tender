"""Shim: browser primitives moved to app.core.browser.primitives."""

from app.core.browser.primitives import *  # noqa: F401,F403
from app.core.browser.primitives import TOOL_HANDLERS

__all__ = ["TOOL_HANDLERS"]
