"""Vision layer: scale, validator, click_on_screen (mocked backend), samples."""

from __future__ import annotations

import base64
import struct
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from playwright.async_api import async_playwright

from app.core.vision import act as vision_act
from app.core.vision.base import GroundingCandidate, GroundingResult, InspectionResult
from app.core.vision.samples import reset_counters_for_tests, write_sample
from app.core.vision.scale import image_to_css, png_pixel_size
from app.core.vision.validator import validate_point

FIXTURE = Path(__file__).parent / "fixtures" / "vision_page.html"


def _minimal_png(width: int, height: int) -> bytes:
    """Tiny valid PNG (IHDR only + IEND) — enough for png_pixel_size."""
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # 1 empty IDAT row for height=1 would be needed for real decode; IHDR is enough for our reader
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


@pytest.fixture
async def vision_rt(tmp_path, monkeypatch):
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
            downloads_dir=tmp_path,
        )
        try:
            yield rt
        finally:
            await browser.close()


def test_png_pixel_size_and_scale() -> None:
    png = _minimal_png(2560, 1800)
    b64 = base64.b64encode(png).decode("ascii")
    assert png_pixel_size(b64) == (2560, 1800)
    # device scale 2: image coords → CSS
    x, y = image_to_css(200, 100, image_size=(2560, 1800), viewport=(1280, 900))
    assert x == 100
    assert y == 50


def test_parse_candidates_clamp_png_then_remap() -> None:
    """PNG 2560×1800, viewport 1280×900, raw (2000,1400) → CSS (1000,700)."""
    from app.core.vision.qwen_vl import _parse_candidates, finalize_candidates

    png = _minimal_png(2560, 1800)
    b64 = base64.b64encode(png).decode("ascii")
    raw = {"found": True, "x": 2000, "y": 1400, "elements": [], "note": ""}
    cands = _parse_candidates(raw, (2560, 1800))
    assert cands[0].x == 2000 and cands[0].y == 1400  # not clamped to 1280
    finalize_candidates(
        cands,
        image_b64=b64,
        viewport=(1280, 900),
        image_size=(2560, 1800),
        coords_mode="pixel",
    )
    assert cands[0].x == 1000
    assert cands[0].y == 700


def test_norm1000_before_remap(monkeypatch) -> None:
    from app.core.vision.qwen_vl import _parse_candidates, finalize_candidates

    png = _minimal_png(2000, 1000)
    b64 = base64.b64encode(png).decode("ascii")
    # 500/1000 * 2000 = 1000 image px → CSS with vp 1000x500 → 500
    raw = {"found": True, "x": 500, "y": 500, "elements": []}
    cands = _parse_candidates(raw, (2000, 1000))
    finalize_candidates(
        cands,
        image_b64=b64,
        viewport=(1000, 500),
        image_size=(2000, 1000),
        coords_mode="norm1000",
    )
    assert cands[0].x == 500
    assert cands[0].y == 250


def test_href_http_skips_hash_and_same_page() -> None:
    from app.core.vision.act import _href_http

    cur = "https://example.com/page"
    assert _href_http({"href": "https://example.com/other"}, cur) == "https://example.com/other"
    assert _href_http({"href": "https://example.com/page#section"}, cur) is None
    assert _href_http({"href": "#"}, cur) is None
    assert _href_http({"href": "javascript:void(0)"}, cur) is None


@pytest.mark.asyncio
async def test_validate_point_statuses(vision_rt) -> None:
    # Button «Документы» center ~ (40+80, 100+20) = (120, 120)
    ok = await validate_point(vision_rt, 120, 120, "кнопка Документы")
    assert ok["status"] == "accepted"
    assert ok["element"]["tag"] == "BUTTON"

    # Same point but wrong target text → mismatch
    bad = await validate_point(vision_rt, 120, 120, "кнопка Выход")
    assert bad["status"] == "rejected_text_mismatch"

    # Empty area
    empty = await validate_point(vision_rt, 700, 500, "Документы")
    assert empty["status"] in {"rejected_no_element", "rejected_not_interactive"}


class _MockBackend:
    name = "mock"

    def __init__(self, candidates: list[GroundingCandidate], found: bool = True) -> None:
        self._candidates = candidates
        self._found = found

    async def ground(self, image_b64, target, viewport) -> GroundingResult:
        return GroundingResult(
            found=self._found,
            candidates=list(self._candidates),
            backend=self.name,
            model="mock",
            note="mock",
        )

    async def inspect(self, image_b64, question, viewport) -> InspectionResult:
        return InspectionResult(answer="no", backend=self.name, model="mock")


@pytest.mark.asyncio
async def test_click_on_screen_navigate_vs_click(vision_rt, tmp_path, monkeypatch) -> None:
    from app import config

    monkeypatch.setattr(config, "VISION_SAMPLES_ENABLED", True)
    monkeypatch.setattr(config, "VISION_SAMPLES_DIR", tmp_path / "samples")
    reset_counters_for_tests()

    # Link «Скачать» → navigate
    link_backend = _MockBackend(
        [GroundingCandidate(x=290, y=120, label="Скачать")]
    )
    nav = await vision_act.click_on_screen(
        vision_rt,
        "ссылка Скачать",
        backend=link_backend,
        run_id="test-run",
        platform="fixture",
    )
    assert nav["action"] == "navigate"
    assert "x" not in nav and "y" not in nav
    assert "example.com" in (nav.get("url") or "")

    # Reset page
    html = FIXTURE.read_text(encoding="utf-8")
    await vision_rt.page.set_content(html, wait_until="domcontentloaded")

    # Button → click
    btn_backend = _MockBackend(
        [GroundingCandidate(x=120, y=120, label="Документы")]
    )
    clk = await vision_act.click_on_screen(
        vision_rt,
        "кнопка Документы",
        backend=btn_backend,
        run_id="test-run",
        platform="fixture",
    )
    assert clk["action"] == "click"
    status = await vision_rt.page.inner_text("#status")
    assert status == "docs-clicked"

    # href="#" → click (not navigate), onclick fires
    html = FIXTURE.read_text(encoding="utf-8")
    await vision_rt.page.set_content(html, wait_until="domcontentloaded")
    # hash-link center ~ (380+60, 100+20) = (440, 120)
    hash_backend = _MockBackend(
        [GroundingCandidate(x=440, y=120, label="Открыть меню")]
    )
    hash_res = await vision_act.click_on_screen(
        vision_rt,
        "ссылка Открыть меню",
        backend=hash_backend,
        run_id="test-run",
    )
    assert hash_res["action"] == "click"
    status = await vision_rt.page.inner_text("#status")
    assert status == "hash-clicked"

    # All rejected → none
    miss_backend = _MockBackend(
        [GroundingCandidate(x=700, y=500, label="nowhere")]
    )
    miss = await vision_act.click_on_screen(
        vision_rt,
        "кнопка Документы",
        backend=miss_backend,
        run_id="test-run",
    )
    assert miss["action"] == "none"
    assert "dom_snapshot" in (miss.get("note") or "").lower() or "rephrase" in (
        miss.get("note") or ""
    ).lower() or "rejected" in (miss.get("note") or "").lower()

    # Samples written
    sample_root = Path(config.VISION_SAMPLES_DIR) / "test-run"
    jsons = list(sample_root.glob("*.json"))
    assert len(jsons) >= 2


@pytest.mark.asyncio
async def test_sample_write_failure_does_not_raise(tmp_path, monkeypatch) -> None:
    from app import config

    monkeypatch.setattr(config, "VISION_SAMPLES_ENABLED", True)
    # Point at a file path so mkdir fails
    bad = tmp_path / "not_a_dir"
    bad.write_text("x", encoding="utf-8")
    monkeypatch.setattr(config, "VISION_SAMPLES_DIR", bad)
    reset_counters_for_tests()
    out = write_sample(
        run_id="r",
        step=1,
        image_b64=base64.b64encode(_minimal_png(8, 8)).decode("ascii"),
        meta={"goal": "x"},
    )
    assert out is None
