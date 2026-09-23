"""Part B: platform toolset composition + DOM escape-hatch behaviour."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from app.agent.tools import PLATFORM_TOOL_NAMES, build_langchain_tools, normalize_tools_mode
from app.agent.tools.high_level import build_high_level_tools
from app.core.browser import dom
from app.domain.dedup import SeenTenderStore


FIXTURE = Path(__file__).parent / "fixtures" / "search_page.html"


def test_normalize_rejects_full() -> None:
    with pytest.raises(ValueError, match="full"):
        normalize_tools_mode("full")
    assert normalize_tools_mode(None) == "platform"
    assert normalize_tools_mode("platform") == "platform"
    assert normalize_tools_mode("browser") == "browser"


def test_platform_toolset_exactly_15(tmp_path) -> None:
    store = SeenTenderStore(":memory:")
    rt = SimpleNamespace(
        page=SimpleNamespace(url="https://example.com/"),
        downloads_dir=tmp_path,
        downloaded_files=[],
    )
    from app.agent.context import make_context

    ctx = make_context(rt, store, "https://example.com/", "test", 3)
    tools = build_langchain_tools(ctx, mode="platform")
    names = [t.name for t in tools]
    assert names == list(PLATFORM_TOOL_NAMES)
    assert len(names) == 15
    assert len(set(names)) == 15
    # also via high_level directly
    assert [t.name for t in build_high_level_tools(ctx)] == list(PLATFORM_TOOL_NAMES)


@pytest.fixture
async def search_rt():
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
async def test_dom_snapshot_query_and_limit(search_rt) -> None:
    filtered = await dom.snapshot_for_agent(search_rt, "Карточка")
    assert filtered["ok"]
    assert all(
        "карточка" in f"{e.get('name') or ''}".lower()
        or "карточка" in f"{e.get('href') or ''}".lower()
        for e in filtered["elements"]
    )
    assert len(filtered["elements"]) >= 1

    full = await dom.snapshot_for_agent(search_rt, None)
    assert full["ok"]
    assert len(full["elements"]) <= 150


@pytest.mark.asyncio
async def test_fill_element_submit(search_rt) -> None:
    inputs = await dom.query(search_rt, placeholder="ключевые")
    assert inputs
    filled = await dom.fill_by_id(search_rt, inputs[0]["id"], "ручки", submit=True)
    assert filled["ok"] is True
    assert filled.get("submit") is True
    status = await search_rt.page.inner_text("#status")
    assert status.startswith("submitted:")
    assert "ручки" in status
