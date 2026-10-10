from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from ..config import Settings
from .navigation import validate_navigation_url


class BrowserRuntime:
    def __init__(self, settings: Settings, downloads_dir: Path) -> None:
        self.settings = settings
        self.downloads_dir = downloads_dir
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def __aenter__(self) -> BrowserRuntime:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.settings.headless)
        assert self._browser is not None
        self.context = await self._browser.new_context(
            accept_downloads=True,
            locale="ru-RU",
            viewport={"width": 1280, "height": 900},
        )
        await self.context.route("**/*", self._guard)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(15_000)
        self.page.set_default_navigation_timeout(20_000)
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self.context is not None:
            await self.context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def _guard(self, route: Any) -> None:
        ok, _reason = validate_navigation_url(route.request.url, self.settings)
        if not ok:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    @property
    def current_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("Браузер не запущен")
        return self.page
