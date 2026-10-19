"""Действия в браузере, которые выбирает модель: переход, клик, ввод, прокрутка, назад."""

from __future__ import annotations

from ..browser.dom_tools import click_element, fill_field, go_back, press_key
from ..browser.navigation import same_site, validate_navigation_url
from ..config import Settings


async def settle(runtime, timeout_ms: int = 2_500) -> None:
    """Дождаться тишины в сети: выдачи и карточки часто дорисовываются скриптом."""
    try:
        await runtime.current_page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


async def goto(runtime, url: str, settings: Settings, host: str) -> None:
    ok, reason = validate_navigation_url(url, settings)
    if not ok:
        raise RuntimeError(reason)
    if not same_site(url, host):
        raise RuntimeError("Переход на другой сайт запрещён.")
    await runtime.current_page.goto(url, wait_until="domcontentloaded")


async def _stay_on_site(runtime, host: str) -> None:
    if not same_site(runtime.current_page.url, host):
        await go_back(runtime)
        raise RuntimeError("Действие увело на другой сайт — вернулся назад.")


async def click(runtime, ref: int, host: str) -> None:
    await click_element(runtime, ref)
    await settle(runtime)
    await _stay_on_site(runtime, host)


async def fill(runtime, ref: int, text: str, submit: bool, host: str) -> None:
    await fill_field(runtime, ref, text)
    if submit:
        await press_key(runtime, "Enter")
        await settle(runtime, 5_000)
        await _stay_on_site(runtime, host)


async def scroll(runtime, direction: str) -> None:
    page = runtime.current_page
    await page.mouse.wheel(0, -900 if direction == "up" else 900)
    await page.wait_for_timeout(400)


async def back(runtime) -> None:
    await go_back(runtime)
    await settle(runtime)
