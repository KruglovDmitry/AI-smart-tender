"""DOM validation of a proposed click point (CSS viewport pixels)."""

from __future__ import annotations

import re
from typing import Any

_INTERACTIVE_CLOSEST = (
    "a,button,input,textarea,select,"
    "[role=button],[role=link],[role=tab],[role=menuitem],[onclick]"
)

_STOP_WORDS = frozenset(
    {
        "кнопка",
        "ссылка",
        "вкладка",
        "поле",
        "иконка",
        "пункт",
        "меню",
        "button",
        "link",
        "tab",
        "the",
        "a",
        "an",
        "на",
        "по",
        "в",
        "и",
        "для",
        "с",
    }
)

_VALIDATE_JS = """([x, y, sel]) => {
  const el0 = document.elementFromPoint(x, y);
  if (!el0) return { hit: null, interactive: null };
  const tag0 = (el0.tagName || '').toUpperCase();
  if (tag0 === 'IFRAME') {
    return {
      hit: { tag: 'IFRAME', role: '', text: '', href: '', aria_label: '', title: '' },
      interactive: null,
      iframe: true,
    };
  }
  const inter = el0.closest(sel);
  const pick = inter || el0;
  const norm = (v) => String(v ?? '').replace(/\\s+/g, ' ').trim().slice(0, 200);
  return {
    hit: {
      tag: (pick.tagName || '').toUpperCase(),
      role: norm(pick.getAttribute('role') || ''),
      text: norm(pick.innerText || pick.textContent || pick.value || ''),
      href: String(pick.href || pick.getAttribute('href') || ''),
      aria_label: norm(pick.getAttribute('aria-label') || ''),
      title: norm(pick.getAttribute('title') || ''),
      value: norm(pick.value || ''),
      placeholder: norm(pick.getAttribute('placeholder') || ''),
    },
    interactive: !!inter,
    iframe: false,
  };
}"""


def _normalize_text(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def significant_words(target: str) -> list[str]:
    """Strip service words; keep tokens with length >= 3."""
    toks = re.findall(r"[a-zA-Zа-яА-Я0-9]+", _normalize_text(target))
    return [t for t in toks if len(t) >= 3 and t not in _STOP_WORDS]


async def validate_point(rt: Any, x: int, y: int, target: str) -> dict[str, Any]:
    """
    Returns {status, element}.
    status: accepted | weak_match | rejected_no_element | rejected_not_interactive
            | rejected_text_mismatch | iframe
    """
    page = rt.page
    x, y = int(x), int(y)
    try:
        raw = await page.evaluate(_VALIDATE_JS, [x, y, _INTERACTIVE_CLOSEST])
    except Exception as e:
        return {
            "status": "rejected_no_element",
            "element": None,
            "error": str(e),
        }

    if not raw or not raw.get("hit"):
        return {"status": "rejected_no_element", "element": None}

    if raw.get("iframe"):
        hit = raw["hit"]
        return {
            "status": "iframe",
            "element": {
                "tag": hit.get("tag"),
                "role": hit.get("role") or "",
                "text": hit.get("text") or "",
                "href": hit.get("href") or "",
                "aria_label": hit.get("aria_label") or "",
                "title": hit.get("title") or "",
            },
        }

    hit = raw["hit"]
    element = {
        "tag": hit.get("tag"),
        "role": hit.get("role") or "",
        "text": hit.get("text") or "",
        "href": hit.get("href") or "",
        "aria_label": hit.get("aria_label") or "",
        "title": hit.get("title") or "",
    }

    if not raw.get("interactive"):
        return {"status": "rejected_not_interactive", "element": element}

    words = significant_words(target)
    if not words:
        return {"status": "weak_match", "element": element}

    blob = _normalize_text(
        " ".join(
            [
                str(hit.get("text") or ""),
                str(hit.get("aria_label") or ""),
                str(hit.get("title") or ""),
                str(hit.get("value") or ""),
                str(hit.get("placeholder") or ""),
            ]
        )
    )
    if any(w in blob for w in words):
        return {"status": "accepted", "element": element}
    return {"status": "rejected_text_mismatch", "element": element}
