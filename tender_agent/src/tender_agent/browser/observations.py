from __future__ import annotations

import hashlib
import re

from ..sites.generic import CAPTCHA_RE, EMPTY_RE, ERROR_RE, FILE_RE
from ..tenders.discovery import bind_controls, fallback_cards, pick_section
from ..tenders.fields import build_fields, cards_from_blocks
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
  const hrefOf = (el) => {
    if (!el) return null;
    const attr = el.getAttribute && el.getAttribute('href');
    if (typeof attr === 'string' && attr) {
      try { return new URL(attr, location.href).href; } catch (e) { return attr; }
    }
    return typeof el.href === 'string' ? el.href : null;
  };
  const linesOf = (el) => String((el && (el.innerText || el.textContent)) || '')
    .replace(/\\u00a0/g, ' ')
    .replace(/[ \\t]+\\n/g, '\\n')
    .replace(/\\n{3,}/g, '\\n\\n')
    .trim();
  const chrome = 'header, nav, footer, aside, [role="navigation"], [role="banner"], [role="contentinfo"]';
  const inChrome = (el) => !!(el && el.closest && el.closest(chrome));
  if (!document.body) return { pending: true };
  const labelFor = (el) => {
    if (el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab) return textOf(lab).slice(0, 160);
    }
    const parent = el.closest('label');
    return parent ? textOf(parent).slice(0, 160) : '';
  };
  const isFile = (el) => {
    const href = hrefOf(el) || '';
    const name = textOf(el);
    return el.hasAttribute('download')
      || /\\.(pdf|docx?|xlsx?|zip|rar|7z)(\\?|$)/i.test(href + ' ' + name)
      || /документ|скачать|файл|вложен|download/i.test(name + ' ' + href);
  };
  const describe = (el, ref) => {
    const tag = (el.tagName || '').toLowerCase();
    el.setAttribute('data-ta-ref', String(ref));
    return {
      ref,
      tag,
      role: el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'button' ? 'button' : ''),
      name: textOf(el).slice(0, 160) || el.getAttribute('aria-label') || el.getAttribute('value') || '',
      href: hrefOf(el),
      placeholder: el.getAttribute('placeholder'),
      label: labelFor(el),
      input_type: tag === 'input' ? (el.getAttribute('type') || 'text') : null,
      value: ('value' in el) ? String(el.value || '').slice(0, 200) : null,
      rel: el.getAttribute('rel'),
      download: el.hasAttribute('download')
    };
  };
  const nodes = [...document.querySelectorAll('a[href], button, input, textarea, select')].filter(visible);
  const controls = nodes.filter(el => (el.tagName || '').toLowerCase() !== 'a');
  const links = nodes.filter(el => (el.tagName || '').toLowerCase() === 'a');
  const ordered = [
    ...controls.slice(0, 40),
    ...links.filter(isFile).slice(0, 25),
    ...links.filter(el => !isFile(el)).slice(0, 25)
  ].slice(0, 80);
  const elements = ordered.map((el, index) => describe(el, index + 1));
  const mainRoot = () => {
    const main = document.querySelector('main');
    if (main && textOf(main).length > 180) return main;
    let best = null;
    let bestScore = 0;
    for (const el of document.querySelectorAll('article, section, div')) {
      if (inChrome(el)) continue;
      const text = textOf(el);
      if (text.length < 180 || text.length > 14000) continue;
      const linkCount = el.querySelectorAll('a').length || 1;
      const score = text.length / linkCount;
      if (score < 40) continue;
      const richer = [...el.children].some(child => {
        if (inChrome(child)) return false;
        const childText = textOf(child);
        if (childText.length < 180 || childText.length > 14000) return false;
        const childLinks = child.querySelectorAll('a').length || 1;
        return childText.length / childLinks > score;
      });
      if (richer) continue;
      if (score > bestScore) {
        best = el;
        bestScore = score;
      }
    }
    return best || document.body;
  };
  const pairsIn = (root) => {
    const found = [];
    const seen = new Set();
    const add = (label, value, source) => {
      label = textOf({ innerText: label }).replace(/[:：]\\s*$/, '').trim();
      value = textOf({ innerText: value }).trim();
      if (label.length < 2 || label.length > 90 || value.length < 1 || value.length > 400) return;
      const key = source + '|' + label.toLowerCase() + '|' + value.toLowerCase();
      if (seen.has(key) || found.length >= 60) return;
      seen.add(key);
      found.push({ label, value, source });
    };
    root.querySelectorAll('dt').forEach(dt => {
      const dd = dt.nextElementSibling;
      if (dd && dd.tagName === 'DD') add(textOf(dt), textOf(dd), 'struct');
    });
    root.querySelectorAll('tr').forEach(tr => {
      const cells = [...tr.children].filter(cell => cell.tagName === 'TH' || cell.tagName === 'TD');
      if (cells.length === 2) add(textOf(cells[0]), textOf(cells[1]), 'struct');
    });
    root.querySelectorAll('div, span, p, li, th, dt, label').forEach(el => {
      const own = textOf(el);
      if (own.length < 2 || own.length > 80 || !/[:：]\\s*$/.test(own)) return;
      const next = el.nextElementSibling;
      if (next) add(own, textOf(next), 'struct');
    });
    root.querySelectorAll('div, section, li, dl').forEach(parent => {
      const kids = [...parent.children];
      if (kids.length < 2 || kids.length > 24) return;
      for (let i = 0; i < kids.length - 1; i++) {
        add(textOf(kids[i]), textOf(kids[i + 1]), 'adjacent');
      }
    });
    return found;
  };
  const noticeHref = (el) => {
    const anchors = [...el.querySelectorAll('a[href]')];
    const numberLink = anchors.find(anchor => /№\\s*\\d{5,}/.test(textOf(anchor)));
    const hrefs = anchors.map(hrefOf).filter(Boolean);
    const bad = /printForm|listModal|signview|organization|\\/search(?:\\/|$)/i;
    const file = /\\.(pdf|docx?|xlsx?|zip|rar|7z)(\\?|$)/i;
    return hrefOf(numberLink)
      || hrefs.find(href => !bad.test(href) && !file.test(href))
      || hrefs.find(Boolean)
      || null;
  };
  const root = mainRoot();
  const content = linesOf(root).slice(0, 12000);
  const pairs = pairsIn(root);
  const blocks = [];
  const seenBlocks = new Set();
  let chosen = [];
  let chosenScore = 0;
  for (const parent of document.querySelectorAll('div, ul, ol, tbody, section')) {
    if (inChrome(parent)) continue;
    const kids = [...parent.children].filter(el => {
      const text = textOf(el);
      return text.length >= 100 && text.length <= 1800 && el.querySelector('a[href]');
    });
    if (kids.length < 3) continue;
    const average = kids.reduce((sum, el) => sum + textOf(el).length, 0) / kids.length;
    const similar = kids.filter(el => {
      const length = textOf(el).length;
      return length > average * 0.45 && length < average * 2.4;
    });
    if (similar.length < 3) continue;
    const dated = similar.filter(el => /\\d{2}\\.\\d{2}\\.\\d{4}/.test(textOf(el)) || /\\d[\\d\\s]{2,}[.,]\\d{2}/.test(textOf(el)));
    const group = dated.length >= 3 ? dated : similar;
    const score = group.length * 1000 - average;
    if (score > chosenScore) {
      chosen = group;
      chosenScore = score;
    }
  }
  for (const el of chosen.slice(0, 12)) {
    const href = noticeHref(el);
    const text = linesOf(el).slice(0, 1800);
    const key = href || text.slice(0, 80);
    if (!key || seenBlocks.has(key)) continue;
    seenBlocks.add(key);
    const numberLink = [...el.querySelectorAll('a[href]')].find(anchor => /№\\s*\\d{5,}/.test(textOf(anchor)));
    blocks.push({
      text,
      href,
      link_text: numberLink ? textOf(numberLink).slice(0, 180) : '',
      pairs: pairsIn(el).slice(0, 20)
    });
  }
  const cardNodes = [...document.querySelectorAll('article.tender-card, tr.tender-row, main[data-tender-id]')];
  const cards = cardNodes.map(el => {
    const cardLinks = [...el.querySelectorAll('a[href]')];
    const pageLink = cardLinks.find(anchor => !anchor.hasAttribute('download') && !/\\.(pdf|docx?|xlsx?|zip)(\\?|$)/i.test(hrefOf(anchor) || ''));
    const price = el.querySelector('.price');
    return {
      id: el.getAttribute('data-id') || el.getAttribute('data-tender-id') || null,
      title: textOf(el.querySelector('.title, h1, h2') || pageLink || el).slice(0, 300),
      href: hrefOf(pageLink) || (el.matches('main') ? location.href : hrefOf(cardLinks[0])),
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
  const headings = [...root.querySelectorAll('h1, h2')].slice(0, 8).map(textOf).filter(Boolean);
  return {
    url: location.href,
    title: document.title || '',
    headings,
    text: content.slice(0, 1500),
    content,
    pairs,
    blocks,
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


def _keep_link(el: Element) -> bool:
    blob = f"{el.name} {el.href or ''}"
    if el.download or FILE_RE.search(el.href or "") or FILE_RE.search(el.name or ""):
        return True
    return bool(re.search(r"документ|скачать|файл|вложен", blob, re.I))


def limit_elements(elements: list[Element], limit: int = 60) -> list[Element]:
    if len(elements) <= limit:
        return elements
    reserved = [el for el in elements if _keep_link(el)][:25]
    reserved_refs = {el.ref for el in reserved}

    def rank(el: Element) -> int:
        if el.tag in {"input", "textarea"}:
            return 0
        if el.tag == "button" or (el.rel or "").lower() == "next":
            return 1
        if el.href:
            return 2
        return 3

    rest = [el for el in elements if el.ref not in reserved_refs]
    return sorted(rest, key=rank)[: limit - len(reserved)] + reserved


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
            "content": "",
            "pairs": [],
            "blocks": [],
        }
    elements = limit_elements([Element.model_validate(item) for item in raw.get("elements") or []])
    content = str(raw.get("content") or "")
    cards = [RawCard.model_validate(item) for item in raw.get("cards") or []]
    if not cards:
        cards = cards_from_blocks(list(raw.get("blocks") or []))
    if not cards:
        cards = fallback_cards(elements)
    obs = Observation(
        url=str(raw.get("url") or runtime.current_page.url),
        title=str(raw.get("title") or ""),
        headings=list(raw.get("headings") or []),
        text_excerpt=(content or str(raw.get("text") or ""))[:1500],
        content_text=content,
        fields=build_fields(list(raw.get("pairs") or []), content),
        elements=elements,
        cards=cards,
    )
    bind_controls(obs, preferred_placeholder)
    obs.page_kind = classify(obs.title, obs.text_excerpt, obs, bool(raw.get("has_password")))
    obs.signature = _signature(obs)
    return obs


async def take_screenshot(runtime: BrowserRuntime) -> bytes:
    return await runtime.current_page.screenshot(type="png")
