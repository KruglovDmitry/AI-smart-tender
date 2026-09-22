"""Shim: session moved to app.core.browser.session."""

from app.core.browser.session import *  # noqa: F401,F403
from app.core.browser.session import BrowserRuntime, browser_runtime

__all__ = ["BrowserRuntime", "browser_runtime"]
