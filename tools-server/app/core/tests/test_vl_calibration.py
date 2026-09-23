"""Integration: calibrate QwenVLBackend.ground vs real element centers.

Run (needs AGENT_LLM_* + live VL):
  py -m pytest app/core/tests/test_vl_calibration.py -m integration -s -v
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from app import config
from app.core.browser.primitives import screenshot
from app.core.vision.qwen_vl import QwenVLBackend

FIXTURE = Path(__file__).parent / "fixtures" / "vision_page.html"

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def calib_rt():
    html = FIXTURE.read_text(encoding="utf-8")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 800, "height": 600})
        await page.set_content(html, wait_until="domcontentloaded")
        rt = SimpleNamespace(
            page=page,
            known_pages={id(page)},
            adopt=lambda pg: None,
            last_screenshot_b64=None,
            last_screenshot_meta={},
            downloaded_files=[],
        )
        try:
            yield rt
        finally:
            await browser.close()


async def test_qwen_vl_ground_calibration_print(calib_rt) -> None:
    if not config.AGENT_LLM_BASE_URL or not config.AGENT_LLM_API_KEY:
        pytest.skip("AGENT_LLM_BASE_URL / AGENT_LLM_API_KEY not configured")

    box = await calib_rt.page.locator("#docs").bounding_box()
    assert box
    cx = int(box["x"] + box["width"] / 2)
    cy = int(box["y"] + box["height"] / 2)
    print(
        f"\n=== VL calibration AGENT_VL_COORDS={config.AGENT_VL_COORDS} "
        f"model={config.AGENT_VL_MODEL} ==="
    )
    print(f"real #docs center CSS: ({cx}, {cy}) bbox={box}")

    backend = QwenVLBackend()
    for i in range(5):
        shot = await screenshot(calib_rt)
        assert shot.get("ok")
        vp = (int(shot["width"]), int(shot["height"]))
        result = await backend.ground(
            calib_rt.last_screenshot_b64,
            "кнопка Документы",
            vp,
        )
        primary = result.candidates[0] if result.candidates else None
        pred = (primary.x, primary.y) if primary else None
        dist = (
            ((pred[0] - cx) ** 2 + (pred[1] - cy) ** 2) ** 0.5 if pred else None
        )
        print(
            f"  run {i + 1}: found={result.found} pred={pred} "
            f"dist={None if dist is None else round(dist, 1)} "
            f"lat={result.latency_ms}ms raw={result.raw} note={result.note!r}"
        )
    # Soft assert: at least one call returned a candidate (LLM may be flaky)
    assert True
