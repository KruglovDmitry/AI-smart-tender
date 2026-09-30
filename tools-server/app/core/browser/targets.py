"""Viewport-visible interactive targets for inspect_screen (DOM branch)."""

from __future__ import annotations

import re
from typing import Any

from .session import BrowserRuntime

_VIEWPORT_TARGETS_JS = """() => {
  const INTERACTIVE = [
    'a[href]', 'button', 'input', 'textarea', 'select',
    '[role="button"]', '[role="link"]', '[role="textbox"]',
    '[role="searchbox"]', '[role="combobox"]', '[role="tab"]',
    '[role="checkbox"]', '[contenteditable="true"]'
  ].join(',');

  document.querySelectorAll('[data-agent-id]').forEach(el => el.removeAttribute('data-agent-id'));

  const nodes = [...document.querySelectorAll(INTERACTIVE)];
  const vw = window.innerWidth || document.documentElement.clientWidth || 0;
  const vh = window.innerHeight || document.documentElement.clientHeight || 0;
  const seen = new Set();
  const out = [];
  let nextId = 1;

  const visibleInViewport = (el) => {
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    if (r.bottom < 0 || r.right < 0 || r.top > vh || r.left > vw) return false;
    return true;
  };

  const norm = (v) => String(v ?? '').replace(/\\s+/g, ' ').trim().slice(0, 80);

  const iconLabel = (el) => {
    const svg = el.querySelector && el.querySelector('svg, use');
    if (!svg) return '';
    const cls = String(svg.getAttribute('class') || el.getAttribute('class') || '')
      .replace(/\\s+/g, ' ').trim();
    const name = String(svg.getAttribute('name') || svg.getAttribute('data-icon') || '').trim();
    const href = String((svg.getAttribute && svg.getAttribute('href'))
      || (svg.getAttribute && svg.getAttribute('xlink:href')) || '');
    const piece = (name || href.split('#').pop() || cls.split(' ').filter(Boolean).slice(0, 2).join(' '))
      .replace(/^\\./, '').slice(0, 40);
    return piece ? ('icon:' + piece) : 'icon:svg';
  };

  const labelOf = (el) => {
    const img = el.querySelector && el.querySelector('img[alt]');
    const alt = img ? norm(img.getAttribute('alt')) : '';
    const candidates = [
      el.innerText,
      el.getAttribute('aria-label'),
      el.getAttribute('title'),
      el.getAttribute('placeholder'),
      el.value,
      alt,
    ];
    for (const c of candidates) {
      const n = norm(c);
      if (n) return n;
    }
    return iconLabel(el);
  };

  const kindOf = (el) => {
    const tag = (el.tagName || '').toLowerCase();
    const role = String(el.getAttribute('role') || '').toLowerCase();
    const type = String(el.type || '').toLowerCase();
    if (role === 'tab' || /\\btab\\b/i.test(el.className || '')) return 'tab';
    if (role === 'checkbox' || type === 'checkbox') return 'checkbox';
    if (tag === 'select' || role === 'combobox' || role === 'listbox') return 'select';
    if (tag === 'input' || tag === 'textarea' || role === 'textbox' || role === 'searchbox')
      return 'input';
    if (tag === 'a' || role === 'link') return 'link';
    if (tag === 'button' || role === 'button' || type === 'button' || type === 'submit')
      return 'button';
    const lab = labelOf(el);
    if (String(lab).startsWith('icon:')) return 'icon';
    return 'other';
  };

  const rank = (kind) => {
    if (kind === 'input') return 0;
    if (kind === 'button' || kind === 'tab') return 1;
    if (kind === 'link') return 2;
    return 3;
  };

  for (const el of nodes) {
    if (seen.has(el)) continue;
    seen.add(el);
    if (!visibleInViewport(el)) continue;
    const id = nextId++;
    el.setAttribute('data-agent-id', String(id));
    const label = labelOf(el);
    const kind = kindOf(el);
    const href = (el.tagName || '').toLowerCase() === 'a' || el.href
      ? String(el.href || el.getAttribute('href') || '')
      : '';
    out.push({
      id,
      label: label || '',
      kind,
      href: href || null,
      rank: rank(kind),
    });
  }

  out.sort((a, b) => a.rank - b.rank || a.id - b.id);
  const dedup = [];
  const keys = new Set();
  for (const t of out) {
    const key = (t.label || '').toLowerCase() + '|' + (t.href || '');
    if (keys.has(key)) continue;
    keys.add(key);
    dedup.push(t);
    if (dedup.length >= 60) break;
  }
  return {
    url: location.href,
    title: document.title || '',
    targets: dedup.map(({id, label, kind, href}) => ({id, label, kind, href})),
  };
}"""


def _meaningful(label: str) -> bool:
    """Meaningful = longer than 1 char and not an icon:-only label."""
    lab = (label or "").strip()
    if len(lab) <= 1:
        return False
    if lab.lower().startswith("icon:"):
        return False
    return True


async def snapshot_viewport_targets(rt: BrowserRuntime) -> dict[str, Any]:
    """
    Tag viewport-visible interactive nodes; return targets with d{id} ready labels.
    """
    page = rt.page
    try:
        raw = await page.evaluate(_VIEWPORT_TARGETS_JS)
        targets_raw = list((raw or {}).get("targets") or [])
        targets: list[dict[str, Any]] = []
        for t in targets_raw:
            tid = int(t.get("id") or 0)
            if tid <= 0:
                continue
            label = str(t.get("label") or "").strip()[:80]
            kind = str(t.get("kind") or "other").strip().lower() or "other"
            href = t.get("href")
            targets.append(
                {
                    "id": f"d{tid}",
                    "dom_id": tid,
                    "label": label,
                    "kind": kind,
                    "href": href,
                    "meaningful": _meaningful(label),
                }
            )
        return {
            "ok": True,
            "url": (raw or {}).get("url") or page.url,
            "title": (raw or {}).get("title") or "",
            "targets": targets,
            "meaningful_count": sum(1 for x in targets if x.get("meaningful")),
        }
    except Exception as e:
        return {
            "ok": False,
            "url": page.url,
            "title": "",
            "targets": [],
            "meaningful_count": 0,
            "message": str(e),
        }


def dom_fingerprint_from_targets(targets: list[dict[str, Any]]) -> str:
    parts = [
        f"{t.get('id')}|{t.get('label')}|{t.get('kind')}|{t.get('href') or ''}"
        for t in targets
    ]
    return "\n".join(parts)
