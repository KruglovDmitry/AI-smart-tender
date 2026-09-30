"""Vision mode toolsets, inspect_screen targets, click_target, after-action."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from playwright.async_api import async_playwright

from app.agent.context import make_context
from app.agent.tools import (
    TOOLS_DOM,
    TOOLS_HYBRID,
    TOOLS_VISION,
    build_langchain_tools,
    normalize_tools_mode,
    normalize_vision_mode,
)
from app.core.browser.after_action import capture_before_state, describe_after_action
from app.core.vision import act as vision_act
from app.core.vision.base import GroundingCandidate, GroundingResult, ScreenTarget
from app.core.vision.perception import parse_targets_payload
from app.domain.dedup import SeenTenderStore
from app.platforms.zakupki_gov_ru import ZakupkiGovRuAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "vision_page.html"
CANVAS_FIXTURE = Path(__file__).parent / "fixtures" / "canvas_page.html"


@pytest.fixture
async def vision_rt(tmp_path):
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


@pytest.fixture
async def canvas_rt(tmp_path):
    html = CANVAS_FIXTURE.read_text(encoding="utf-8")
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


class _FakePerception:
    name = "fake_perception"
    model = "fake"

    async def list_targets(self, image_b64, viewport):
        return [
            ScreenTarget(label="Документация закупки", kind="tab"),
            ScreenTarget(label="иконка скрепки у строки 2", kind="icon"),
        ]

    async def ground(self, *a, **k):
        raise AssertionError("perception must not ground")

    async def inspect(self, *a, **k):
        from app.core.vision.base import InspectionResult

        return InspectionResult(answer="ok", backend=self.name, model=self.model, latency_ms=1)


class _FakeGrounding:
    name = "fake_ground"
    model = "fake"
    calls: list[str]

    def __init__(self):
        self.calls = []

    async def ground(self, image_b64, goal, viewport):
        self.calls.append(goal)
        return GroundingResult(
            found=True,
            candidates=[GroundingCandidate(x=100, y=120, label=goal)],
            action="click",
            note="",
            backend=self.name,
            model=self.model,
            latency_ms=1,
        )

    async def list_targets(self, *a, **k):
        return []

    async def inspect(self, *a, **k):
        from app.core.vision.base import InspectionResult

        return InspectionResult(answer="", backend=self.name, model=self.model, latency_ms=1)


def test_normalize_rejects_browser() -> None:
    with pytest.raises(ValueError, match="browser"):
        normalize_tools_mode("browser")
    assert normalize_tools_mode(None) == "platform"


def test_toolsets_per_vision_mode(tmp_path) -> None:
    store = SeenTenderStore(":memory:")
    rt = SimpleNamespace(
        page=SimpleNamespace(url="https://example.com/"),
        downloads_dir=tmp_path,
        downloaded_files=[],
    )
    ctx = make_context(rt, store, "https://example.com/", "test", 3)
    for mode, expected in (
        ("dom", TOOLS_DOM),
        ("hybrid", TOOLS_HYBRID),
        ("vision", TOOLS_VISION),
    ):
        ctx.vision_mode = mode
        names = [t.name for t in build_langchain_tools(ctx, vision_mode=mode)]
        assert names == list(expected), mode


def test_vision_mode_normalize() -> None:
    assert normalize_vision_mode(None) == "hybrid"
    assert normalize_vision_mode("VISION") == "vision"
    with pytest.raises(ValueError):
        normalize_vision_mode("nope")


@pytest.mark.asyncio
async def test_inspect_screen_dom_labels(vision_rt) -> None:
    result = await vision_act.inspect_screen(vision_rt, mode="dom")
    assert result["ok"]
    assert result["source"] == "dom"
    labels = {t["label"] for t in result["targets"]}
    assert "Документы" in labels
    assert "Скачать" in labels
    assert any(str(t["label"]).startswith("icon:") for t in result["targets"])
    assert all(t["id"].startswith("d") for t in result["targets"])


@pytest.mark.asyncio
async def test_inspect_screen_dom_plus_vision(canvas_rt, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.config.perception_configured", lambda: True, raising=False
    )
    result = await vision_act.inspect_screen(
        canvas_rt, mode="hybrid", perception=_FakePerception()
    )
    assert result["ok"]
    assert result["source"] in {"dom+vision", "vision"}
    ids = [t["id"] for t in result["targets"]]
    assert any(i.startswith("v") for i in ids)


@pytest.mark.asyncio
async def test_click_target_d_skips_grounding(vision_rt) -> None:
    insp = await vision_act.inspect_screen(vision_rt, mode="hybrid")
    bag = {
        "url": insp["_url"],
        "fingerprint": insp["_fingerprint"],
        "targets": insp["_internal_targets"],
    }
    docs = next(t for t in bag["targets"] if t["label"] == "Документы")
    ground = _FakeGrounding()
    result = await vision_act.click_target(
        vision_rt,
        docs["id"],
        mode="hybrid",
        screen_targets=bag,
        grounding=ground,
    )
    assert result["ok"]
    assert ground.calls == []
    status = await vision_rt.page.inner_text("#status")
    assert status == "docs-clicked"


@pytest.mark.asyncio
async def test_click_target_v_uses_label(vision_rt) -> None:
    box = await vision_rt.page.locator("#docs").bounding_box()
    assert box
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2

    class _G:
        calls: list[str] = []

        async def ground(self, image_b64, goal, viewport):
            self.calls.append(goal)
            return GroundingResult(
                found=True,
                candidates=[
                    GroundingCandidate(x=x, y=y, label=goal)
                ],
                action="click",
                note="",
                backend="fake",
                model="fake",
                latency_ms=1,
            )

        async def list_targets(self, *a, **k):
            return []

        async def inspect(self, *a, **k):
            from app.core.vision.base import InspectionResult

            return InspectionResult(
                answer="", backend="fake", model="fake", latency_ms=1
            )

    bag_v = {
        "url": vision_rt.page.url,
        "fingerprint": "shot:1",
        "targets": [
            {"id": "v1", "label": "Документы", "kind": "button", "origin": "vision"}
        ],
    }
    ground = _G()
    result = await vision_act.click_target(
        vision_rt,
        "v1",
        mode="vision",
        screen_targets=bag_v,
        grounding=ground,
    )
    assert result["ok"]
    assert ground.calls == ["Документы"]
    status = await vision_rt.page.inner_text("#status")
    assert status == "docs-clicked"


@pytest.mark.asyncio
async def test_stale_targets_after_navigate(vision_rt) -> None:
    insp = await vision_act.inspect_screen(vision_rt, mode="dom")
    bag = {
        "url": insp["_url"],
        "fingerprint": insp["_fingerprint"],
        "targets": insp["_internal_targets"],
    }
    await vision_rt.page.goto("about:blank")
    docs = bag["targets"][0]
    result = await vision_act.click_target(
        vision_rt, docs["id"], mode="dom", screen_targets=bag
    )
    assert result["ok"] is False
    assert "inspect_screen" in (result.get("note") or "")


@pytest.mark.asyncio
async def test_describe_after_action_hybrid(vision_rt) -> None:
    before = await capture_before_state(vision_rt, mode="hybrid")
    await vision_rt.page.evaluate(
        "() => { document.body.innerHTML += '<a href=\"https://x/file.pdf\">PDF</a><p>новый блок</p>'; }"
    )
    report = await describe_after_action(vision_rt, before, mode="hybrid")
    assert report["file_links"] > 0
    assert report["new_text"]


@pytest.mark.asyncio
async def test_describe_after_action_vision(vision_rt) -> None:
    before = await capture_before_state(vision_rt, mode="vision")
    assert before.get("screenshot_b64")
    # Dramatic full-viewport paint so screen_diff is unambiguous
    await vision_rt.page.evaluate(
        """() => {
          document.body.innerHTML = '';
          document.body.style.margin = '0';
          const d = document.createElement('div');
          d.style.cssText = 'width:800px;height:600px;background:#ff0000;';
          document.body.appendChild(d);
        }"""
    )
    report = await describe_after_action(vision_rt, before, mode="vision")
    assert report["screen_diff"] > 0.02
    assert report["changed"] is True


@pytest.mark.asyncio
async def test_type_into_target_submit(vision_rt) -> None:
    await vision_rt.page.evaluate(
        """() => {
          const f = document.createElement('form');
          f.id = 'f';
          f.addEventListener('submit', (e) => {
            e.preventDefault();
            document.getElementById('status').textContent = 'form:' + document.getElementById('search').value;
          });
          const inp = document.getElementById('search');
          inp.parentNode.insertBefore(f, inp);
          f.appendChild(inp);
        }"""
    )
    insp = await vision_act.inspect_screen(vision_rt, mode="hybrid")
    bag = {
        "url": insp["_url"],
        "fingerprint": insp["_fingerprint"],
        "targets": insp["_internal_targets"],
    }
    inp = next(t for t in bag["targets"] if t.get("kind") == "input")
    result = await vision_act.type_into_target(
        vision_rt, inp["id"], "ручки", submit=True, mode="hybrid", screen_targets=bag
    )
    assert result["ok"]
    status = await vision_rt.page.inner_text("#status")
    assert "ручки" in status


def test_parse_list_targets() -> None:
    raw = '[{"label": "Документы", "kind": "tab"}, {"label": "x", "kind": "bogus"}]'
    out = parse_targets_payload(raw)
    assert out[0].kind == "tab"
    assert out[1].kind == "other"


def test_eis_documents_url_and_flag(monkeypatch) -> None:
    ad = ZakupkiGovRuAdapter()
    u44 = (
        "https://zakupki.gov.ru/epz/order/notice/zk20/common-info.html"
        "?regNumber=0123456789012345678"
    )
    docs = ad.documents_url(u44)
    assert docs and "documents.html" in docs and "regNumber=" in docs
    u223 = (
        "https://zakupki.gov.ru/epz/order/notice/notice223/common-info.html"
        "?regNumber=32310000000"
    )
    assert ad.documents_url(u223) is None  # needs noticeGuid
    monkeypatch.setattr("app.config.EIS_TEST_NO_DOCS_ROUTE", True)
    assert ad.documents_url(u44) is None


def test_auto_vision_switch_tracking(tmp_path) -> None:
    """Simulate DOM-empty → vision-helped streak reaching threshold."""
    from app.agent.tools.high_level import _note_dom_blind

    store = SeenTenderStore(":memory:")
    rt = SimpleNamespace(
        page=SimpleNamespace(url="https://example.com/"),
        downloads_dir=tmp_path,
        downloaded_files=[],
    )
    ctx = make_context(rt, store, "https://example.com/", "test", 3)
    ctx.vision_mode = "hybrid"
    for _ in range(3):
        _note_dom_blind(ctx, "dom_snapshot", {"ok": True, "elements": []})
        _note_dom_blind(
            ctx,
            "click_target",
            {"ok": True, "changed": True, "target_id": "v1"},
        )
    assert ctx.dom_blind_streak == 3


def test_vision_without_perception_raises(monkeypatch) -> None:
    monkeypatch.setattr("app.config.perception_configured", lambda: False)
    monkeypatch.setattr("app.config.AGENT_PERCEPTION_BACKEND", "qwen_vl")
    from app.core.vision.registry import get_perception_backend

    with pytest.raises(RuntimeError, match="perception"):
        get_perception_backend("qwen_vl")
