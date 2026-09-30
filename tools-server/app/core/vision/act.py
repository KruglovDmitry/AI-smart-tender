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
    from .registry import get_vision_backend

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
    question: str | None = None,
    *,
    mode: str = "hybrid",
    perception: VisionBackend | None = None,
    grounding: VisionBackend | None = None,
) -> dict[str, Any]:
    """
    List clickable/typeable targets for the primary model.

    mode=dom|hybrid: DOM viewport targets (d…); hybrid may add perception (v…) if <5 meaningful.
    mode=vision: perception only (v…).
    Optional question → answer from perception backend.
    """
    from ... import config
    from ..browser.targets import dom_fingerprint_from_targets, snapshot_viewport_targets
    from .registry import get_perception_backend, get_vision_backend

    mode = (mode or "hybrid").strip().lower()
    if mode not in {"dom", "hybrid", "vision"}:
        mode = "hybrid"

    url = getattr(rt.page, "url", "") or ""
    try:
        title = await rt.page.title()
    except Exception:
        title = ""
    try:
        kind_info = await detect_page_kind(rt)
        page_kind = str(kind_info.get("page_kind") or "unknown")
    except Exception:
        page_kind = "unknown"

    targets: list[dict[str, Any]] = []
    source = "dom"
    notes: list[str] = []
    fingerprint = ""

    if mode in {"dom", "hybrid"}:
        snap = await snapshot_viewport_targets(rt)
        if not snap.get("ok"):
            notes.append(str(snap.get("message") or "dom snapshot failed"))
        for t in snap.get("targets") or []:
            targets.append(
                {
                    "id": t["id"],
                    "label": t.get("label") or "",
                    "kind": t.get("kind") or "other",
                    "href": t.get("href"),
                    "dom_id": t.get("dom_id"),
                    "origin": "dom",
                }
            )
        fingerprint = dom_fingerprint_from_targets(targets)
        meaningful = int(snap.get("meaningful_count") or 0)
        need_vision = mode == "hybrid" and meaningful < 5
    else:
        need_vision = True
        meaningful = 0

    answer = None
    perc_backend = perception
    if need_vision or (question and str(question).strip()):
        if perc_backend is None:
            try:
                perc_backend = get_perception_backend()
            except Exception as e:
                notes.append(f"perception backend: {e}")
                perc_backend = None
        configured = bool(config.perception_configured())
        if mode == "vision" and not configured:
            return {
                "ok": False,
                "mode": mode,
                "url": url,
                "title": title,
                "page_kind": page_kind,
                "source": "vision",
                "targets": [],
                "note": "perception не настроен (AGENT_PERCEPTION_* / UI_TARS)",
            }

        shot = await core_screenshot(rt)
        if not shot.get("ok") or not getattr(rt, "last_screenshot_b64", None):
            if mode == "vision":
                return {
                    "ok": False,
                    "mode": mode,
                    "url": url,
                    "title": title,
                    "page_kind": page_kind,
                    "source": "vision",
                    "targets": [],
                    "note": "screenshot failed",
                }
            notes.append("screenshot failed; DOM-only targets")
        else:
            vp = (
                int(shot.get("width") or 1280),
                int(shot.get("height") or 900),
            )
            image_b64 = rt.last_screenshot_b64
            if mode == "vision":
                fingerprint = f"shot:{hash(image_b64) & 0xFFFFFFFF:08x}"
            if need_vision:
                if configured or perc_backend is not None:
                    try:
                        if perc_backend is None:
                            perc_backend = get_perception_backend()
                        listed = await perc_backend.list_targets(image_b64, vp)
                        for i, st in enumerate(listed, start=1):
                            targets.append(
                                {
                                    "id": f"v{i}",
                                    "label": st.label,
                                    "kind": st.kind,
                                    "href": None,
                                    "origin": "vision",
                                }
                            )
                        source = "dom+vision" if mode == "hybrid" else "vision"
                        if mode == "vision":
                            source = "vision"
                    except Exception as e:
                        notes.append(f"list_targets failed: {e}")
                        if mode == "hybrid":
                            notes.append("perception не настроен или ошибка — только DOM")
                else:
                    notes.append("perception не настроен")
            if question and str(question).strip() and perc_backend is not None:
                try:
                    insp = await perc_backend.inspect(
                        image_b64, str(question).strip(), vp
                    )
                    answer = insp.answer
                except Exception as e:
                    answer = f"inspect error: {e}"

    # Agent-facing targets: strip internal fields
    public = [
        {"id": t["id"], "label": t.get("label") or "", "kind": t.get("kind") or "other"}
        for t in targets
    ]
    return {
        "ok": True,
        "mode": mode,
        "url": url,
        "title": title,
        "page_kind": page_kind,
        "source": source if targets else ("dom" if mode != "vision" else "vision"),
        "targets": public,
        "answer": answer,
        "note": "; ".join(notes) if notes else None,
        # for ctx persistence (caller may strip)
        "_internal_targets": targets,
        "_fingerprint": fingerprint,
        "_url": url,
    }


async def click_target(
    rt: Any,
    target_id: str,
    *,
    mode: str = "hybrid",
    screen_targets: dict[str, Any] | None = None,
    grounding: VisionBackend | None = None,
    run_id: str | None = None,
    platform: str = "",
) -> dict[str, Any]:
    """
    Click a target from the last inspect_screen list.
    d… → DOM click_by_id; v… → UI-TARS ground by label.
    """
    from ... import config
    from ..browser.after_action import capture_before_state, describe_after_action
    from ..browser.targets import dom_fingerprint_from_targets, snapshot_viewport_targets
    from .registry import get_vision_backend

    mode = (mode or "hybrid").strip().lower()
    tid = (target_id or "").strip()
    bag = screen_targets or {}
    stored_url = str(bag.get("url") or "")
    stored_fp = str(bag.get("fingerprint") or "")
    items = list(bag.get("targets") or [])

    # Stale check
    cur_url = getattr(rt.page, "url", "") or ""
    if stored_url and cur_url.split("#")[0] != stored_url.split("#")[0]:
        return {
            "ok": False,
            "note": "экран изменился, вызови inspect_screen снова",
            "reason": "url_changed",
        }
    if mode != "vision" and stored_fp:
        snap = await snapshot_viewport_targets(rt)
        now_fp = dom_fingerprint_from_targets(snap.get("targets") or [])
        # Compare labels set loosely — full fingerprint mismatch
        if now_fp != stored_fp:
            return {
                "ok": False,
                "note": "экран изменился, вызови inspect_screen снова",
                "reason": "fingerprint_changed",
            }

    hit = next((t for t in items if str(t.get("id")) == tid), None)
    if hit is None:
        return {"ok": False, "note": f"unknown target_id={tid!r}; call inspect_screen"}

    before = await capture_before_state(rt, mode=mode)
    download_info = None

    if tid.startswith("d"):
        dom_id = hit.get("dom_id")
        if dom_id is None:
            try:
                dom_id = int(str(tid)[1:])
            except ValueError:
                return {"ok": False, "note": f"bad dom target id {tid}"}
        # Listen for download while clicking
        page = rt.page
        try:
            async with page.expect_download(timeout=3_000) as dl_info:
                click_res = await browser_dom.click_by_id(rt, int(dom_id))
            try:
                dl = await dl_info.value
                path = await dl.path()
                # Caller/high_level may move into tender dir; report basename for now
                download_info = {
                    "file": str(path or dl.suggested_filename or ""),
                    "suggested_name": dl.suggested_filename,
                    "bytes": None,
                }
            except Exception:
                pass
        except Exception:
            click_res = await browser_dom.click_by_id(rt, int(dom_id))
        if not click_res.get("ok"):
            return {
                "ok": False,
                "note": click_res.get("message") or "click_by_id failed",
                **click_res,
            }
    elif tid.startswith("v"):
        label = str(hit.get("label") or "").strip()
        if not label:
            return {"ok": False, "note": "empty vision target label"}
        backend = grounding or get_vision_backend()
        # Use click_on_screen path for navigate|click with validation
        allow_dom = mode != "vision" or bool(
            getattr(config, "VISION_ALLOW_DOM_CHECKS", False)
        )
        if mode == "vision" and not allow_dom:
            # Ground + click without DOM validator
            shot = await core_screenshot(rt)
            if not shot.get("ok") or not rt.last_screenshot_b64:
                return {"ok": False, "note": "screenshot failed"}
            vp = (int(shot.get("width") or 1280), int(shot.get("height") or 900))
            grounded = await backend.ground(rt.last_screenshot_b64, label, vp)
            if grounded.action == "not_found" or not grounded.candidates:
                return {
                    "ok": False,
                    "note": grounded.note or "not_found",
                    "action": "not_found",
                }
            c0 = grounded.candidates[0]
            w, h = vp
            if not (0 <= c0.x < w and 0 <= c0.y < h):
                return {"ok": False, "note": "point outside viewport"}
            try:
                await rt.page.mouse.click(c0.x, c0.y)
                await rt.page.wait_for_timeout(350)
            except Exception as e:
                return {"ok": False, "note": str(e)}
        else:
            click_res = await click_on_screen(
                rt,
                label,
                backend=backend,
                run_id=run_id,
                platform=platform,
            )
            if not click_res.get("ok"):
                return {
                    "ok": False,
                    "note": click_res.get("note") or "vision click failed",
                    **{k: v for k, v in click_res.items() if k not in {"x", "y"}},
                }
    else:
        return {"ok": False, "note": f"unsupported target_id={tid!r}"}

    report = await describe_after_action(
        rt, before, mode=mode, download=download_info
    )

    # vision: if nothing changed, retry once with next grounding candidate
    if (
        mode == "vision"
        and tid.startswith("v")
        and not report.get("changed")
        and not download_info
    ):
        label = str(hit.get("label") or "").strip()
        backend = grounding or get_vision_backend()
        shot = await core_screenshot(rt)
        if shot.get("ok") and rt.last_screenshot_b64:
            vp = (int(shot.get("width") or 1280), int(shot.get("height") or 900))
            grounded = await backend.ground(rt.last_screenshot_b64, label, vp)
            cands = list(grounded.candidates or [])
            if len(cands) > 1:
                c1 = cands[1]
                w, h = vp
                if 0 <= c1.x < w and 0 <= c1.y < h:
                    before2 = await capture_before_state(rt, mode=mode)
                    try:
                        await rt.page.mouse.click(c1.x, c1.y)
                        await rt.page.wait_for_timeout(350)
                    except Exception as e:
                        return {
                            "ok": False,
                            "note": f"retry click failed: {e}",
                            "hint": "inspect_screen снова или scroll",
                            **report,
                        }
                    report = await describe_after_action(
                        rt, before2, mode=mode, download=None
                    )
                    if report.get("changed"):
                        return {
                            "ok": True,
                            "target_id": tid,
                            "label": hit.get("label"),
                            "retried": True,
                            **report,
                        }
        return {
            "ok": False,
            "note": "клик не изменил экран; попробуй другую цель или scroll",
            "hint": "inspect_screen снова",
            "target_id": tid,
            **report,
        }

    return {"ok": True, "target_id": tid, "label": hit.get("label"), **report}


async def type_into_target(
    rt: Any,
    target_id: str,
    text: str,
    *,
    submit: bool = False,
    mode: str = "hybrid",
    screen_targets: dict[str, Any] | None = None,
    grounding: VisionBackend | None = None,
    run_id: str | None = None,
    platform: str = "",
) -> dict[str, Any]:
    """Click target, Ctrl+A, type text; optional Enter. Returns after-action report."""
    from ..browser.after_action import capture_before_state, describe_after_action

    click_res = await click_target(
        rt,
        target_id,
        mode=mode,
        screen_targets=screen_targets,
        grounding=grounding,
        run_id=run_id,
        platform=platform,
    )
    if not click_res.get("ok"):
        return click_res

    before = await capture_before_state(rt, mode=mode)
    page = rt.page
    try:
        await page.keyboard.press("Control+A")
        await page.keyboard.type(text or "", delay=15)
        if submit:
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(400)
        else:
            await page.wait_for_timeout(150)
    except Exception as e:
        return {"ok": False, "note": f"type failed: {e}", "target_id": target_id}

    report = await describe_after_action(rt, before, mode=mode, download=None)
    return {
        "ok": True,
        "target_id": target_id,
        "typed": True,
        "submit": bool(submit),
        **report,
    }


async def scroll_screen(
    rt: Any,
    direction: str = "down",
    amount: str = "screen",
) -> dict[str, Any]:
    """Mouse-wheel scroll at viewport center; return screen_diff + url."""
    from ..browser.after_action import _screen_diff_ratio
    from ..browser.primitives import screenshot as core_shot

    direction = (direction or "down").strip().lower()
    amount = (amount or "screen").strip().lower()
    if direction not in {"down", "up"}:
        direction = "down"
    if amount not in {"screen", "half"}:
        amount = "screen"

    before_shot = await core_shot(rt)
    b64_a = rt.last_screenshot_b64 or ""
    vp = rt.page.viewport_size or {}
    w = int(vp.get("width") or 1280)
    h = int(vp.get("height") or 900)
    delta = h if amount == "screen" else max(1, h // 2)
    if direction == "up":
        delta = -delta
    try:
        await rt.page.mouse.move(w // 2, h // 2)
        await rt.page.mouse.wheel(0, delta)
        await rt.page.wait_for_timeout(200)
    except Exception as e:
        return {"ok": False, "note": str(e), "url": getattr(rt.page, "url", "")}

    after_shot = await core_shot(rt)
    b64_b = rt.last_screenshot_b64 or ""
    diff = 0.0
    if b64_a and b64_b:
        diff = _screen_diff_ratio(str(b64_a), str(b64_b))
    return {
        "ok": True,
        "screen_diff": round(diff, 4),
        "url": getattr(rt.page, "url", "") or "",
        "direction": direction,
        "amount": amount,
        "stale_targets": True,
    }


def strip_coords(result: dict[str, Any]) -> dict[str, Any]:
    """Ensure agent-facing payload has no x/y."""
    out = {
        k: v
        for k, v in result.items()
        if k not in {
            "x",
            "y",
            "candidates",
            "chosen",
            "_internal_targets",
            "_fingerprint",
            "_url",
        }
    }
    return out
