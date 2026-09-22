"""Shim: page_kind moved to app.core.browser.page_kind."""

from app.core.browser.page_kind import *  # noqa: F401,F403
from app.core.browser.page_kind import classify_page_kind, detect_page_kind

__all__ = ["classify_page_kind", "detect_page_kind"]
