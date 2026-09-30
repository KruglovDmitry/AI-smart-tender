"""Post-action page reports for hybrid/dom/vision modes."""

from __future__ import annotations

import base64
import io
from typing import Any

from .page_kind import detect_page_kind
from .primitives import list_download_links
from .session import BrowserRuntime


async def _page_meta(rt: BrowserRuntime) -> dict[str, Any]:
    url = ""
    title = ""
    try:
        url = rt.page.url or ""
    except Exception:
        pass
    try:
        title = await rt.page.title()
    except Exception:
        title = ""
    kind = "unknown"
    try:
        info = await detect_page_kind(rt)
        kind = str(info.get("page_kind") or "unknown")
    except Exception:
        pass
    return {"url": url, "title": title, "page_kind": kind}


async def _inner_text_lines(rt: BrowserRuntime, limit: int = 400) -> list[str]:
    try:
        text = await rt.page.evaluate(
            """() => (document.body && (document.body.innerText || '')) || ''"""
        )
    except Exception:
        return []
    lines = [
        re_line
        for re_line in (
            " ".join(str(line).split()) for line in str(text or "").splitlines()
        )
        if re_line
    ]
    return lines[:limit]


def _new_text(before_lines: list[str], after_lines: list[str], max_chars: int = 300) -> str:
    before_set = set(before_lines)
    fresh = [ln for ln in after_lines if ln not in before_set]
    blob = " | ".join(fresh)
    return blob[:max_chars]


def _screen_diff_ratio(b64_a: str, b64_b: str, width: int = 320, thresh: int = 25) -> float:
    """Fraction of pixels that differ on downscaled grayscale-ish RGB sum."""
    try:
        from PIL import Image
    except ImportError:
        return 0.0
    try:
        ia = Image.open(io.BytesIO(base64.b64decode(b64_a))).convert("RGB")
        ib = Image.open(io.BytesIO(base64.b64decode(b64_b))).convert("RGB")
    except Exception:
        return 0.0
    ia = ia.resize((width, max(1, int(width * ia.height / max(ia.width, 1)))))
    ib = ib.resize(ia.size)
    pa = list(ia.getdata())
    pb = list(ib.getdata())
    if not pa:
        return 0.0
    changed = 0
    for (r1, g1, b1), (r2, g2, b2) in zip(pa, pb):
        if abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2) > thresh:
            changed += 1
    return changed / float(len(pa))


async def describe_after_action(
    rt: BrowserRuntime,
    before: dict[str, Any],
    *,
    mode: str = "hybrid",
    download: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build post-action report.
    `before` keys: url, title, page_kind, text_lines?, screenshot_b64?
    """
    mode = (mode or "hybrid").strip().lower()
    after = await _page_meta(rt)
    url_changed = (before.get("url") or "") != (after.get("url") or "")
    dl = download if isinstance(download, dict) else None

    if mode == "vision":
        screen_diff = 0.0
        b64_a = before.get("screenshot_b64") or ""
        # Always capture a fresh after-shot (do not reuse before's last_screenshot_b64).
        b64_b = ""
        try:
            from .primitives import screenshot

            shot = await screenshot(rt)
            if shot.get("ok"):
                b64_b = rt.last_screenshot_b64 or ""
        except Exception:
            b64_b = ""
        if b64_a and b64_b:
            screen_diff = _screen_diff_ratio(str(b64_a), str(b64_b))
        changed = bool(url_changed or screen_diff > 0.02 or dl)
        return {
            "changed": changed,
            "url_changed": url_changed,
            "url": after["url"],
            "title": after["title"],
            "screen_diff": round(screen_diff, 4),
            "download": dl,
        }

    # hybrid / dom
    file_links = 0
    try:
        links = await list_download_links(rt, limit=40)
        file_links = int(links.get("count") or len(links.get("links") or []) or 0)
    except Exception:
        file_links = 0
    after_lines = await _inner_text_lines(rt)
    before_lines = list(before.get("text_lines") or [])
    new_text = _new_text(before_lines, after_lines)
    changed = bool(
        url_changed
        or (after.get("page_kind") != before.get("page_kind"))
        or bool(new_text)
        or bool(dl)
        or file_links > int(before.get("file_links") or 0)
    )
    return {
        "changed": changed,
        "url_changed": url_changed,
        "url": after["url"],
        "title": after["title"],
        "page_kind": after["page_kind"],
        "file_links": file_links,
        "new_text": new_text,
        "download": dl,
    }


async def capture_before_state(rt: BrowserRuntime, *, mode: str = "hybrid") -> dict[str, Any]:
    meta = await _page_meta(rt)
    mode = (mode or "hybrid").strip().lower()
    state: dict[str, Any] = {**meta, "text_lines": [], "file_links": 0, "screenshot_b64": ""}
    if mode == "vision":
        from .primitives import screenshot

        shot = await screenshot(rt)
        if shot.get("ok"):
            state["screenshot_b64"] = rt.last_screenshot_b64 or ""
        return state
    state["text_lines"] = await _inner_text_lines(rt)
    try:
        links = await list_download_links(rt, limit=40)
        state["file_links"] = int(links.get("count") or len(links.get("links") or []) or 0)
    except Exception:
        state["file_links"] = 0
    return state
