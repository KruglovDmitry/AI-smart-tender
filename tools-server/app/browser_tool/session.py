"""Playwright browser session for the tender browser agent."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from .. import config

logger = logging.getLogger(__name__)


@dataclass
class BrowserRuntime:
    playwright: Playwright
    context: BrowserContext
    page: Page
    downloads_dir: Path
    known_pages: set[int] = field(default_factory=set)
    downloaded_files: list[str] = field(default_factory=list)
    last_screenshot_b64: str | None = None
    last_screenshot_meta: dict[str, Any] = field(default_factory=dict)

    def adopt(self, page: Page) -> None:
        self.page = page
        self.known_pages.add(id(page))


@asynccontextmanager
async def browser_runtime(
    downloads_dir: Path | None = None,
) -> AsyncIterator[BrowserRuntime]:
    downloads = Path(downloads_dir or config.BROWSER_DOWNLOADS_DIR)
    downloads.mkdir(parents=True, exist_ok=True)
    config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    playwright = await async_playwright().start()
    context: BrowserContext | None = None
    try:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.BROWSER_PROFILE_DIR),
            headless=config.BROWSER_HEADLESS,
            accept_downloads=True,
            locale="ru-RU",
            viewport={
                "width": config.BROWSER_VIEWPORT_WIDTH,
                "height": config.BROWSER_VIEWPORT_HEIGHT,
            },
            args=[
                f"--window-size={config.BROWSER_VIEWPORT_WIDTH},"
                f"{config.BROWSER_VIEWPORT_HEIGHT}",
                "--disable-dev-shm-usage",
            ],
            ignore_https_errors=True,
        )
        context.set_default_timeout(config.BROWSER_NAV_TIMEOUT_MS)
        context.set_default_navigation_timeout(config.BROWSER_NAV_TIMEOUT_MS)

        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.set_viewport_size(
                {
                    "width": config.BROWSER_VIEWPORT_WIDTH,
                    "height": config.BROWSER_VIEWPORT_HEIGHT,
                }
            )
        except Exception:
            pass

        runtime = BrowserRuntime(
            playwright=playwright,
            context=context,
            page=page,
            downloads_dir=downloads,
            known_pages={id(page)},
        )
        yield runtime
    finally:
        if context is not None:
            await context.close()
        await playwright.stop()
