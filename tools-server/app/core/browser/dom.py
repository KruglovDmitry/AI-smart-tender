"""DOM-first primitives: tag interactive elements and act by id (no coordinates)."""

from __future__ import annotations

import re
from typing import Any

from .session import BrowserRuntime

# Injected once per snapshot: stamp data-agent-id on interactive nodes, return descriptors.
_SNAPSHOT_JS = """() => {
  const INTERACTIVE = [
    'a[href]', 'button', 'input', 'textarea', 'select',
    '[role="button"]', '[role="link"]', '[role="textbox"]',
    '[role="searchbox"]', '[role="combobox"]', '[role="tab"]',
    '[contenteditable="true"]'
  ].join(',');

  document.querySelectorAll('[data-agent-id]').forEach(el => el.removeAttribute('data-agent-id'));

  const nodes = [...document.querySelectorAll(INTERACTIVE)];
  const seen = new Set();
  const elements = [];
  let nextId = 1;

  const visible = (el) => {
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const norm = (v) => String(v ?? '').replace(/\\s+/g, ' ').trim().slice(0, 160);

  for (const el of nodes) {
    if (seen.has(el)) continue;
    seen.add(el);
    const id = nextId++;
    el.setAttribute('data-agent-id', String(id));
    const tag = (el.tagName || '').toLowerCase();
    const role = norm(el.getAttribute('role') || '')
      || (tag === 'a' ? 'link'
        : tag === 'button' ? 'button'
        : tag === 'input' ? (el.type === 'search' ? 'searchbox' : 'textbox')
        : tag === 'textarea' ? 'textbox'
        : tag === 'select' ? 'combobox'
        : '');
    const name = norm(
      el.getAttribute('aria-label')
      || el.getAttribute('title')
      || el.innerText
      || el.textContent
      || el.value
      || el.getAttribute('alt')
      || ''
    );
    const href = (tag === 'a' || el.href) ? String(el.href || el.getAttribute('href') || '') : '';
    const placeholder = norm(el.getAttribute('placeholder') || '');
    const r = el.getBoundingClientRect();
    elements.push({
      id,
      tag,
      role,
      name,
      href: href || null,
      placeholder: placeholder || null,
      type: tag === 'input' ? (el.type || 'text') : null,
      bbox: {
        x: Math.round(r.x),
        y: Math.round(r.y),
        w: Math.round(r.width),
        h: Math.round(r.height),
      },
      visible: visible(el),
    });
  }
  return { url: location.href, count: elements.length, elements };
}"""


def _ok(action: str, message: str, **data: Any) -> dict[str, Any]:
    return {"ok": True, "action": action, "message": message, **data}


def _err(action: str, message: str, **data: Any) -> dict[str, Any]:
    return {"ok": False, "action": action, "message": message, **data}


async def snapshot_interactive(rt: BrowserRuntime) -> dict[str, Any]:
    """
    Tag interactive DOM nodes with data-agent-id and return descriptors.
    Shape: {ok, url, elements:[{id, tag, role, name, href, placeholder, bbox, visible}]}
    """
    page = rt.page
    try:
        raw = await page.evaluate(_SNAPSHOT_JS)
        elements = list((raw or {}).get("elements") or [])
        return _ok(
            "snapshot_interactive",
            f"Tagged {len(elements)} interactive elements",
            url=(raw or {}).get("url") or page.url,
            elements=elements,
        )
    except Exception as e:
        return _err("snapshot_interactive", str(e), url=page.url, elements=[])


async def click_by_id(rt: BrowserRuntime, el_id: int) -> dict[str, Any]:
    """Click element previously tagged by snapshot_interactive."""
    page = rt.page
    el_id = int(el_id)
    try:
        url_before = page.url
        handle = await page.query_selector(f'[data-agent-id="{el_id}"]')
        if handle is None:
            return _err("click_by_id", f"No element with data-agent-id={el_id}", id=el_id)

        try:
            await handle.scroll_into_view_if_needed(timeout=5_000)
        except Exception:
            pass

        ctx = page.context
        pages_before = list(ctx.pages)
        await handle.click(timeout=10_000)
        await page.wait_for_timeout(300)

        fresh = [p for p in ctx.pages if p not in pages_before and not p.is_closed()]
        if not fresh:
            fresh = [
                p
                for p in ctx.pages
                if not p.is_closed() and id(p) not in rt.known_pages
            ]
        new_tab = False
        if fresh:
            new_page = fresh[-1]
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass
            rt.adopt(new_page)
            new_tab = True

        url_after = rt.page.url
        changed = new_tab or (url_after != url_before)
        # Also treat hash/title changes and in-page interactions as soft change
        if not changed:
            try:
                # value/focus side-effects: element still exists
                still = await rt.page.query_selector(f'[data-agent-id="{el_id}"]')
                changed = still is None  # navigated away / replaced
            except Exception:
                pass

        return _ok(
            "click_by_id",
            f"Clicked element id={el_id}"
            + (" -> new tab" if new_tab else "")
            + ("" if changed or new_tab else " (URL unchanged)"),
            id=el_id,
            changed=bool(changed or new_tab),
            new_tab=new_tab,
            url=rt.page.url,
        )
    except Exception as e:
        return _err("click_by_id", str(e), id=el_id, url=page.url)


async def fill_by_id(
    rt: BrowserRuntime,
    el_id: int,
    text: str,
    *,
    submit: bool = False,
) -> dict[str, Any]:
    """Focus + fill text into element tagged by snapshot_interactive."""
    page = rt.page
    el_id = int(el_id)
    text = "" if text is None else str(text)
    try:
        handle = await page.query_selector(f'[data-agent-id="{el_id}"]')
        if handle is None:
            return _err("fill_by_id", f"No element with data-agent-id={el_id}", id=el_id)

        try:
            await handle.scroll_into_view_if_needed(timeout=5_000)
        except Exception:
            pass

        tag = (await handle.evaluate("el => (el.tagName || '').toLowerCase()")).lower()
        await handle.click(timeout=5_000)
        if tag in {"input", "textarea"} or await handle.get_attribute("contenteditable"):
            await handle.fill(text)
        else:
            # fallback: select-all + type
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await page.keyboard.type(text, delay=15)

        if submit:
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(250)

        value = await handle.evaluate(
            """el => {
              if (el.value != null) return String(el.value);
              return (el.innerText || el.textContent || '').trim();
            }"""
        )
        return _ok(
            "fill_by_id",
            f"Filled element id={el_id} ({len(text)} chars)"
            + (" + Enter" if submit else ""),
            id=el_id,
            text=text,
            value=value,
            submit=bool(submit),
            url=page.url,
        )
    except Exception as e:
        return _err("fill_by_id", str(e), id=el_id, url=page.url)


async def query(
    rt: BrowserRuntime,
    *,
    role: str | None = None,
    text: str | None = None,
    placeholder: str | None = None,
    href_re: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    """
    Filter last snapshot (or take a fresh one) by role/text/placeholder/href_re/query.
    `query` matches against name/placeholder/role/tag (case-insensitive substring).
    Returns matching element dicts (same shape as snapshot items).
    """
    snap = await snapshot_interactive(rt)
    elements = list(snap.get("elements") or [])
    href_pat = re.compile(href_re, re.I) if href_re else None
    role_l = (role or "").strip().lower() or None
    text_l = (text or "").strip().lower() or None
    ph_l = (placeholder or "").strip().lower() or None
    q_l = (query or "").strip().lower() or None

    out: list[dict[str, Any]] = []
    for el in elements:
        if role_l and (el.get("role") or "").lower() != role_l:
            # also allow tag alias: role=link matches <a>
            if not (role_l == "link" and el.get("tag") == "a"):
                if not (role_l == "button" and el.get("tag") == "button"):
                    if not (
                        role_l in {"textbox", "searchbox"}
                        and el.get("tag") in {"input", "textarea"}
                    ):
                        continue
        if text_l:
            blob = f"{el.get('name') or ''} {el.get('placeholder') or ''}".lower()
            if text_l not in blob:
                continue
        if ph_l and ph_l not in (el.get("placeholder") or "").lower():
            continue
        if href_pat is not None:
            href = el.get("href") or ""
            if not href_pat.search(href):
                continue
        if q_l:
            blob = " ".join(
                [
                    str(el.get("name") or ""),
                    str(el.get("placeholder") or ""),
                    str(el.get("role") or ""),
                    str(el.get("tag") or ""),
                    str(el.get("href") or ""),
                ]
            ).lower()
            if q_l not in blob:
                continue
        out.append(el)
    return out


def prioritize_elements(elements: list[dict[str, Any]], *, limit: int = 150) -> list[dict[str, Any]]:
    """inputs → buttons/tabs → links with text; cap at limit."""

    def rank(el: dict[str, Any]) -> tuple[int, int]:
        tag = (el.get("tag") or "").lower()
        role = (el.get("role") or "").lower()
        name = (el.get("name") or "").strip()
        if tag in {"input", "textarea"} or role in {"textbox", "searchbox", "combobox"}:
            return (0, el.get("id") or 0)
        if tag == "button" or role in {"button", "tab", "menuitem"}:
            return (1, el.get("id") or 0)
        if tag == "a" or role == "link":
            return (2 if name else 3, el.get("id") or 0)
        return (4, el.get("id") or 0)

    ordered = sorted(elements, key=rank)
    return ordered[: max(1, int(limit))]


async def snapshot_for_agent(
    rt: BrowserRuntime,
    query_str: str | None = None,
    *,
    limit: int = 150,
) -> dict[str, Any]:
    """
    Agent-facing DOM snapshot: optional query filter; without query — prioritized ≤ limit.
    """
    q = (query_str or "").strip() or None
    if q:
        elements = await query(rt, query=q)
        return _ok(
            "dom_snapshot",
            f"query={q!r}: {len(elements)} matches",
            url=rt.page.url,
            query=q,
            elements=elements,
            count=len(elements),
        )
    snap = await snapshot_interactive(rt)
    if not snap.get("ok"):
        return snap
    full = list(snap.get("elements") or [])
    elements = prioritize_elements(full, limit=limit)
    return _ok(
        "dom_snapshot",
        f"{len(elements)}/{len(full)} interactive elements (prioritized)",
        url=snap.get("url") or rt.page.url,
        elements=elements,
        count=len(elements),
        total=len(full),
    )
