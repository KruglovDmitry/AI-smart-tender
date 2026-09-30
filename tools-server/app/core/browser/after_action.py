"""Post-action page reports for DOM/platform tools."""

from __future__ import annotations

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


async def describe_after_action(
    rt: BrowserRuntime,
    before: dict[str, Any],
    *,
    mode: str = "dom",
    download: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build post-action report (DOM/text signals)."""
    _ = mode
    after = await _page_meta(rt)
    url_changed = (before.get("url") or "") != (after.get("url") or "")
    dl = download if isinstance(download, dict) else None

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


async def capture_before_state(rt: BrowserRuntime, *, mode: str = "dom") -> dict[str, Any]:
    _ = mode
    meta = await _page_meta(rt)
    state: dict[str, Any] = {**meta, "text_lines": [], "file_links": 0}
    state["text_lines"] = await _inner_text_lines(rt)
    try:
        links = await list_download_links(rt, limit=40)
        state["file_links"] = int(links.get("count") or len(links.get("links") or []) or 0)
    except Exception:
        state["file_links"] = 0
    return state
