"""DOM-first primitives against a static HTML fixture (Playwright, no network)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from app.core.browser import dom

FIXTURE = Path(__file__).parent / "fixtures" / "search_page.html"


@pytest.fixture
async def rt_page():
    html = FIXTURE.read_text(encoding="utf-8")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 800, "height": 600})
        await page.set_content(html, wait_until="domcontentloaded")
        rt = SimpleNamespace(page=page, known_pages={id(page)}, adopt=lambda pg: None)
        try:
            yield rt
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_snapshot_finds_search_and_links(rt_page) -> None:
    snap = await dom.snapshot_interactive(rt_page)
    assert snap["ok"] is True
    els = snap["elements"]
    assert any(
        (e.get("placeholder") or "").lower().find("ключ") >= 0
        or (e.get("role") or "") == "searchbox"
        for e in els
    )
    links = [e for e in els if e.get("tag") == "a" and e.get("href")]
    assert len(links) >= 2


@pytest.mark.asyncio
async def test_query_by_placeholder_and_href(rt_page) -> None:
    found = await dom.query(rt_page, placeholder="ключевые")
    assert found, "search input by placeholder"
    assert found[0]["tag"] in {"input"}

    cards = await dom.query(rt_page, role="link", text="Карточка А")
    assert cards, "card link by text"
    assert "111" in (cards[0].get("href") or "")

    by_href = await dom.query(rt_page, href_re=r"/tender/222/")
    assert by_href and "222" in (by_href[0].get("href") or "")


@pytest.mark.asyncio
async def test_fill_and_click_without_coordinates(rt_page) -> None:
    inputs = await dom.query(rt_page, placeholder="ключевые")
    assert inputs
    filled = await dom.fill_by_id(rt_page, inputs[0]["id"], "канцтовары")
    assert filled["ok"] is True
    assert "канцтовары" in (filled.get("value") or "")

    status = await rt_page.page.inner_text("#status")
    assert status == "idle"

    cards = await dom.query(rt_page, text="Карточка А", role="link")
    assert cards
    clicked = await dom.click_by_id(rt_page, cards[0]["id"])
    assert clicked["ok"] is True
    status = await rt_page.page.inner_text("#status")
    assert status == "opened:111"
