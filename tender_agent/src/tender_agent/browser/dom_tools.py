from __future__ import annotations

from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..tenders.models import Element, Observation
from .runtime import BrowserRuntime


def _locator(runtime: BrowserRuntime, ref: int):
    return runtime.current_page.locator(f'[data-ta-ref="{int(ref)}"]')


async def click_element(runtime: BrowserRuntime, ref: int) -> None:
    locator = _locator(runtime, ref)
    if await locator.count() == 0:
        raise RuntimeError(f"Элемент {ref} нет на текущей странице.")
    await locator.click(timeout=8_000)
    try:
        await runtime.current_page.wait_for_load_state("domcontentloaded", timeout=1_500)
    except PlaywrightTimeout:
        return


async def fill_field(runtime: BrowserRuntime, ref: int, value: str) -> None:
    locator = _locator(runtime, ref)
    if await locator.count() == 0:
        raise RuntimeError(f"Поле {ref} нет на текущей странице.")
    await locator.fill(value)
    actual = await locator.input_value()
    if actual != value:
        raise RuntimeError("Поле не сохранило введённое значение.")


async def press_key(runtime: BrowserRuntime, key: str) -> None:
    await runtime.current_page.keyboard.press(key)


async def scroll_page(runtime: BrowserRuntime) -> None:
    await runtime.current_page.mouse.wheel(0, 900)


async def go_back(runtime: BrowserRuntime) -> None:
    await runtime.current_page.go_back(wait_until="domcontentloaded")


async def switch_to_new_tab(runtime: BrowserRuntime, known_ids: set[int]) -> bool:
    assert runtime.context is not None
    fresh = [page for page in runtime.context.pages if id(page) not in known_ids and not page.is_closed()]
    if not fresh:
        return False
    runtime.page = fresh[-1]
    try:
        await runtime.page.wait_for_load_state("domcontentloaded", timeout=8_000)
    except PlaywrightTimeout:
        return True
    return True


def element_by_ref(obs: Observation, ref: int | None) -> Element | None:
    if ref is None:
        return None
    for el in obs.elements:
        if el.ref == ref:
            return el
    return None
