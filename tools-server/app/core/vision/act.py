"""Orchestrate ground → validate → navigate|click (no coords exposed to agent)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urldefrag, urlparse

from ..browser import dom as browser_dom
from ..browser.page_kind import detect_page_kind
from ..browser.primitives import navigate as core_navigate
from ..browser.primitives import screenshot as core_screenshot
from .base import GroundingCandidate, VisionBackend
from .samples import next_step, write_sample
from .validator import validate_point

logger = logging.getLogger(__name__)

_ACCEPT_STATUSES = frozenset({"accepted", "iframe", "weak_match"})


async def ground_validated(
    rt: Any,
    goal: str,
    *,
    backend: VisionBackend | None = None,
    run_id: str | None = None,
    platform: str = "",
) -> dict[str, Any]:
    """
    Screenshot → ground → validate_point. Does NOT click.

    For platform adapters that need a verified point before click_xy / type_text.
    Writes a vision sample when run_id is set (same dataset as click_on_screen).
    """
    from . import get_vision_backend

    goal = (goal or "").strip()
    shot = await core_screenshot(rt)
    if not shot.get("ok") or not getattr(rt, "last_screenshot_b64", None):
        return {
            "ok": False,
            "x": None,
            "y": None,
            "validation": None,
            "element": None,
            "note": "screenshot failed",
            "action": "none",
        }

    image_b64 = rt.last_screenshot_b64
    vp = (
        int(shot.get("width") or 1280),
        int(shot.get("height") or 900),
    )
    be = backend or get_vision_backend()
    grounded = await be.ground(image_b64, goal, vp)

    if getattr(grounded, "action", None) == "not_found" or not grounded.candidates:
        note = grounded.note or (
            "not_found" if grounded.action == "not_found" else "grounding found nothing"
        )
        if run_id:
            step = next_step(run_id)
            if step is not None:
                write_sample(
                    run_id=run_id,
                    step=step,
                    image_b64=image_b64,
                    meta={
                        "platform": platform,
                        "url": getattr(rt.page, "url", ""),
                        "goal": goal,
                        "backend": grounded.backend,
                        "model": grounded.model,
                        "viewport": list(vp),
                        "candidates": [],
                        "chosen": None,
                        "validation": None,
                        "action": grounded.action or "none",
                        "source": "ground_validated",
                        "latency_ms": grounded.latency_ms,
                        "raw_response": getattr(grounded, "raw_response", "") or "",
                    },
                )
        return {
            "ok": False,
            "x": None,
            "y": None,
            "validation": None,
            "element": None,
            "note": note,
            "action": grounded.action or "none",
            "backend": grounded.backend,
            "model": grounded.model,
            "latency_ms": grounded.latency_ms,
        }

    chosen: GroundingCandidate | None = None
    validation: dict[str, Any] | None = None
    for cand in grounded.candidates[:3]:
        validation = await validate_point(rt, cand.x, cand.y, goal)
        if validation.get("status") in _ACCEPT_STATUSES:
            chosen = cand
            break

    if chosen is None:
        note = "all candidates rejected by validate_point"
        if validation:
            note = f"{note}: {validation.get('status')}"
        if run_id:
            step = next_step(run_id)
            if step is not None:
                write_sample(
                    run_id=run_id,
                    step=step,
                    image_b64=image_b64,
                    meta={
                        "platform": platform,
                        "url": getattr(rt.page, "url", ""),
                        "goal": goal,
                        "backend": grounded.backend,
                        "model": grounded.model,
                        "viewport": list(vp),
                        "candidates": [
                            {"x": c.x, "y": c.y, "label": c.label}
                            for c in grounded.candidates[:3]
                        ],
                        "chosen": None,
                        "validation": validation,
                        "action": "rejected",
                        "source": "ground_validated",
                        "latency_ms": grounded.latency_ms,
                        "raw_response": getattr(grounded, "raw_response", "") or "",
                    },
                )
        return {
            "ok": False,
            "x": None,
            "y": None,
            "validation": (validation or {}).get("status") if validation else None,
            "element": (validation or {}).get("element") if validation else None,
            "note": note,
            "action": "rejected",
            "backend": grounded.backend,
            "model": grounded.model,
            "latency_ms": grounded.latency_ms,
        }

    if run_id:
        step = next_step(run_id)
        if step is not None:
            write_sample(
                run_id=run_id,
                step=step,
                image_b64=image_b64,
                meta={
                    "platform": platform,
                    "url": getattr(rt.page, "url", ""),
                    "goal": goal,
                    "backend": grounded.backend,
                    "model": grounded.model,
                    "viewport": list(vp),
                    "candidates": [
                        {"x": c.x, "y": c.y, "label": c.label}
                        for c in grounded.candidates[:3]
                    ],
                    "chosen": {"x": chosen.x, "y": chosen.y, "label": chosen.label},
                    "validation": validation,
                    "action": "validated",
                    "source": "ground_validated",
                    "latency_ms": grounded.latency_ms,
                    "raw_response": getattr(grounded, "raw_response", "") or "",
                },
            )

    return {
        "ok": True,
        "x": chosen.x,
        "y": chosen.y,
        "validation": (validation or {}).get("status"),
        "element": (validation or {}).get("element"),
        "note": grounded.note or "",
        "action": "click",
        "backend": grounded.backend,
        "model": grounded.model,
        "latency_ms": grounded.latency_ms,
    }


async def _dom_fingerprint(rt: Any) -> str:
    try:
        snap = await browser_dom.snapshot_interactive(rt)
        els = snap.get("elements") or []
        name_len = sum(len(str(e.get("name") or "")) for e in els)
        try:
            body_len = int(
                await rt.page.evaluate(
                    "() => (document.body && (document.body.innerText || '') || '').length"
                )
                or 0
            )
        except Exception:
            body_len = 0
        return f"{len(els)}:{name_len}:{body_len}"
    except Exception:
        return "0:0:0"


async def _page_kind_safe(rt: Any) -> str:
    try:
        info = await detect_page_kind(rt)
        return str(info.get("page_kind") or "")
    except Exception:
        return ""


def _url_no_fragment(url: str) -> str:
    base, _frag = urldefrag(url or "")
    return base


def _href_http(
    element: dict[str, Any] | None,
    current_url: str = "",
) -> str | None:
    """
    Return href for navigate only when it is a real http(s) navigation target
    that differs from the current page (ignoring #fragment).
    Same-page anchors like href="#" → None (caller should mouse-click).
    """
    if not element:
        return None
    href = str(element.get("href") or "").strip()
    if not href:
        return None
    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"}:
        return None
    if _url_no_fragment(href) == _url_no_fragment(current_url):
        return None
    return href


async def click_on_screen(
    rt: Any,
    goal: str,
    *,
    backend: VisionBackend,
    run_id: str | None = None,
    platform: str = "",
    max_candidates: int = 3,
) -> dict[str, Any]:
    """
    Screenshot → ground → validate candidates → navigate|click|none.
    Result never includes x/y.
    """
    goal = (goal or "").strip()
    shot = await core_screenshot(rt)
    if not shot.get("ok") or not getattr(rt, "last_screenshot_b64", None):
        return {
            "ok": False,
            "action": "none",
            "validation": None,
            "element": None,
            "changed": False,
            "url": getattr(rt.page, "url", ""),
            "page_kind": await _page_kind_safe(rt),
            "note": "screenshot failed",
        }

    image_b64 = rt.last_screenshot_b64
    vp = (
        int(shot.get("width") or 1280),
        int(shot.get("height") or 900),
    )
    url_before = rt.page.url
    kind_before = await _page_kind_safe(rt)
    fp_before = await _dom_fingerprint(rt)

    grounded = await backend.ground(image_b64, goal, vp)
    candidates: list[GroundingCandidate] = list(grounded.candidates or [])[:max_candidates]

    chosen: GroundingCandidate | None = None
    validation: dict[str, Any] | None = None
    action = "none"
    nav_or_click_ok = False
    note = grounded.note or ""

    # Model explicitly said element absent — do not click a guessed point
    if getattr(grounded, "action", None) == "not_found" and not candidates:
        result = {
            "ok": False,
            "action": "not_found",
            "validation": None,
            "element": None,
            "changed": False,
            "url": url_before,
            "page_kind": kind_before,
            "note": note or "vision: not_found",
            "backend": grounded.backend,
            "model": grounded.model,
            "latency_ms": grounded.latency_ms,
        }
        if run_id:
            step = next_step(run_id)
            if step is not None:
                write_sample(
                    run_id=run_id,
                    step=step,
                    image_b64=image_b64,
                    meta={
                        "platform": platform,
                        "url": url_before,
                        "page_kind": kind_before,
                        "goal": goal,
                        "backend": grounded.backend,
                        "model": grounded.model,
                        "viewport": list(vp),
                        "candidates": [],
                        "chosen": None,
                        "validation": None,
                        "action": "not_found",
                        "changed": False,
                        "latency_ms": grounded.latency_ms,
                        "auto_label": None,
                        "label": None,
                        "raw_response": getattr(grounded, "raw_response", "") or "",
                    },
                )
        return result

    for cand in candidates:
        validation = await validate_point(rt, cand.x, cand.y, goal)
        status = validation.get("status")
        if status not in _ACCEPT_STATUSES:
            continue
        chosen = cand
        element = validation.get("element")
        href = _href_http(
            element if isinstance(element, dict) else None,
            current_url=url_before,
        )

        if href:
            nav = await core_navigate(rt, href)
            action = "navigate"
            nav_or_click_ok = bool(nav.get("ok"))
            note = (note + " " if note else "") + f"navigate({href[:120]})"
            if status == "weak_match":
                note += " [weak_match]"
        else:
            try:
                await rt.page.mouse.click(cand.x, cand.y)
                await rt.page.wait_for_timeout(350)
                action = "click"
                nav_or_click_ok = True
                if status == "weak_match":
                    note = (note + " " if note else "") + "[weak_match]"
            except Exception as e:
                action = "none"
                nav_or_click_ok = False
                note = f"click failed: {e}"
        break

    if chosen is None:
        note = (
            note
            or "all candidates rejected — try dom_snapshot(query=…) or rephrase goal"
        )
        if candidates:
            note = (
                "all candidates rejected — try dom_snapshot(query=…) or rephrase goal. "
                + note
            )
        elif not grounded.found:
            note = note or "grounding found nothing"

    url_after = rt.page.url
    kind_after = await _page_kind_safe(rt)
    fp_after = await _dom_fingerprint(rt)
    changed = bool(
        action != "none"
        and (
            url_after != url_before
            or kind_after != kind_before
            or fp_after != fp_before
        )
    )

    result = {
        "ok": bool(action != "none" and nav_or_click_ok),
        "action": action,
        "validation": (validation or {}).get("status") if validation else None,
        "element": (validation or {}).get("element") if validation else None,
        "changed": changed,
        "url": url_after,
        "page_kind": kind_after,
        "note": note.strip(),
        "backend": grounded.backend,
        "model": grounded.model,
        "latency_ms": grounded.latency_ms,
    }

    if run_id:
        step = next_step(run_id)
        if step is not None:
            auto_label = None
            if (validation or {}).get("status") == "accepted" and changed:
                auto_label = "weak_positive"
            write_sample(
                run_id=run_id,
                step=step,
                image_b64=image_b64,
                meta={
                    "platform": platform,
                    "url": url_before,
                    "page_kind": kind_before,
                    "goal": goal,
                    "backend": grounded.backend,
                    "model": grounded.model,
                    "viewport": list(vp),
                    "candidates": [
                        {"x": c.x, "y": c.y, "label": c.label} for c in candidates
                    ],
                    "chosen": (
                        {"x": chosen.x, "y": chosen.y, "label": chosen.label}
                        if chosen
                        else None
                    ),
                    "validation": validation,
                    "action": action,
                    "changed": changed,
                    "latency_ms": grounded.latency_ms,
                    "auto_label": auto_label,
                    "label": None,
                },
            )

    return result


async def inspect_screen(
    rt: Any,
    question: str,
    *,
    backend: VisionBackend,
) -> dict[str, Any]:
    shot = await core_screenshot(rt)
    if not shot.get("ok") or not getattr(rt, "last_screenshot_b64", None):
        return {"ok": False, "answer": "screenshot failed"}
    vp = (
        int(shot.get("width") or 1280),
        int(shot.get("height") or 900),
    )
    insp = await backend.inspect(rt.last_screenshot_b64, question, vp)
    return {
        "ok": True,
        "answer": insp.answer,
        "backend": insp.backend,
        "model": insp.model,
        "latency_ms": insp.latency_ms,
    }


def strip_coords(result: dict[str, Any]) -> dict[str, Any]:
    """Ensure agent-facing payload has no x/y."""
    out = {k: v for k, v in result.items() if k not in {"x", "y", "candidates", "chosen"}}
    return out
