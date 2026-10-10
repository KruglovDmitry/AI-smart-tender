from __future__ import annotations

import hashlib

from ..sites.generic import CAPTCHA_RE, EMPTY_RE, ERROR_RE
from ..tenders.discovery import bind_controls, fallback_cards, pick_section
from ..tenders.models import Element, Observation, RawCard
from .runtime import BrowserRuntime

_SNAPSHOT_JS = """() => {
  document.querySelectorAll('[data-ta-ref]').forEach(el => el.removeAttribute('data-ta-ref'));
  const visible = (el) => {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };
  const textOf = (el) => {
    if (!el || typeof el !== 'object') return '';
    return String(el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
  };
  if (!document.body) return { pending: true };
  const labelFor = (el) => {
    if (el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab) return textOf(lab).slice(0, 160);
    }
    const parent = el.closest('label');
    return parent ? textOf(parent).slice(0, 160) : '';
  };
  const nodes = [...document.querySelectorAll('a[href], button, input, textarea, select')].filter(visible);
  const elements = [];
  let next = 1;
  for (const el of nodes) {
    if (elements.length >= 80) break;
    const ref = next++;
    el.setAttribute('data-ta-ref', String(ref));
    const tag = (el.tagName || '').toLowerCase();
    elements.push({
      ref,
      tag,
      role: el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'button' ? 'button' : ''),
      name: textOf(el).slice(0, 160) || el.getAttribute('aria-label') || el.getAttribute('value') || '',
      href: el.href || el.getAttribute('href') || null,
      placeholder: el.getAttribute('placeholder'),
      label: labelFor(el),
      input_type: tag === 'input' ? (el.getAttribute('type') || 'text') : null,
      value: ('value' in el) ? String(el.value || '').slice(0, 200) : null,
      rel: el.getAttribute('rel'),
      download: el.hasAttribute('download')
    });
  }
  const cardNodes = [...document.querySelectorAll('article.tender-card, tr.tender-row, main[data-tender-id]')];
  const cards = cardNodes.map(el => {
    const links = [...el.querySelectorAll('a[href]')];
    const pageLink = links.find(a => !a.hasAttribute('download') && !/\\.(pdf|docx?|xlsx?|zip)(\\?|$)/i.test(a.getAttribute('href') || ''));
    const price = el.querySelector('.price');
    return {
      id: el.getAttribute('data-id') || el.getAttribute('data-tender-id') || null,
      title: textOf(el.querySelector('.title, h1, h2') || pageLink || el).slice(0, 300),
      href: (pageLink && pageLink.href) || (el.matches('main') ? location.href : ((links[0] && links[0].href) || null)),
      customer: textOf(el.querySelector('.customer') || '').slice(0, 200) || null,
      published_at: el.getAttribute('data-published') || textOf(el.querySelector('.published') || '') || null,
      deadline: el.getAttribute('data-deadline') || textOf(el.querySelector('.deadline') || '') || null,
      price_text: price ? textOf(price) : null,
      amount: price ? price.getAttribute('data-amount') : null,
      currency: price ? price.getAttribute('data-currency') : null,
      status: textOf(el.querySelector('.status') || '') || null,
      sources: {
        title: 'dom',
        published_at: el.hasAttribute('data-published') ? 'data-published' : 'text'
      }
    };
  });
  const headings = [...document.querySelectorAll('h1, h2')].slice(0, 8).map(textOf).filter(Boolean);
  return {
    url: location.href,
    title: document.title || '',
    headings,
    text: textOf(document.body).slice(0, 1500),
    has_password: !!document.querySelector('input[type="password"]'),
    elements,
    cards
  };
}"""


def _signature(obs: Observation) -> str:
    titles = "|".join((card.title or "") for card in obs.cards[:8])
    raw = f"{obs.url}|{obs.page_kind}|{titles}|{obs.search_value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def classify(title: str, text: str, obs: Observation, has_password: bool) -> str:
    if CAPTCHA_RE.search(f"{title} {text}"):
        return "captcha"
    if has_password:
        return "login"
    if ERROR_RE.search(title):
        return "error"
    single = any(card.sources.get("title") == "dom" and card.href == obs.url for card in obs.cards)
    main_card = len(obs.cards) == 1 and obs.cards[0].href == obs.url
    if main_card and obs.file_refs:
        return "documents"
    if main_card or single and len(obs.cards) == 1 and not obs.search_ref:
        return "tender_card"
    if obs.cards:
        return "tender_list"
    if EMPTY_RE.search(text):
        return "empty"
    if obs.search_ref:
        return "search"
    if pick_section(obs.elements):
        return "home"
    return "unknown"


def limit_elements(elements: list[Element], limit: int = 60) -> list[Element]:
    if len(elements) <= limit:
        return elements

    def rank(el: Element) -> int:
        if el.tag in {"input", "textarea"}:
            return 0
        if el.tag == "button" or (el.rel or "").lower() == "next":
            return 1
        if el.href:
            return 2
        return 3

    return sorted(elements, key=rank)[:limit]


def _snapshot_retryable(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "innertext" in message
        or "execution context was destroyed" in message
        or "navigation" in message
    )


async def get_page_state(runtime: BrowserRuntime, preferred_placeholder: str | None = None) -> Observation:
    page = runtime.current_page
    raw: dict | None = None
    last_error: Exception | None = None
    for _ in range(8):
        try:
            loaded = await page.evaluate(_SNAPSHOT_JS)
        except Exception as exc:
            if not _snapshot_retryable(exc):
                raise
            last_error = exc
            await page.wait_for_timeout(400)
            continue
        if isinstance(loaded, dict) and not loaded.get("pending"):
            raw = loaded
            break
        await page.wait_for_timeout(400)
    if raw is None:
        if last_error is not None:
            raise last_error
        raw = {
            "url": page.url,
            "title": "",
            "headings": [],
            "text": "",
            "has_password": False,
            "elements": [],
            "cards": [],
        }
    elements = limit_elements([Element.model_validate(item) for item in raw.get("elements") or []])
    cards = [RawCard.model_validate(item) for item in raw.get("cards") or []]
    if not cards:
        cards = fallback_cards(elements)
    obs = Observation(
        url=str(raw.get("url") or runtime.current_page.url),
        title=str(raw.get("title") or ""),
        headings=list(raw.get("headings") or []),
        text_excerpt=str(raw.get("text") or ""),
        elements=elements,
        cards=cards,
    )
    bind_controls(obs, preferred_placeholder)
    obs.page_kind = classify(obs.title, obs.text_excerpt, obs, bool(raw.get("has_password")))
    obs.signature = _signature(obs)
    return obs


async def take_screenshot(runtime: BrowserRuntime) -> bytes:
    return await runtime.current_page.screenshot(type="png")
