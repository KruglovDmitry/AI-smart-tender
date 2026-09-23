"""Rosatom (zakupki.rosatom.ru) SPA adapter — URL search + deep DOM + vision fallback."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse, urlunparse

from ..core.browser import dom as browser_dom
from ..core.browser.page_kind import detect_page_kind
from ..core.browser.primitives import list_download_links, navigate
from ..core.browser.url_utils import bump_page_url
from ..core.vision.act import ground_validated
from .base import CardRef, DocRef, SearchSpec, StepResult

logger = logging.getLogger(__name__)

# Heuristic SPA adapter — selectors/URL patterns are unconfirmed without a live
# platform log. Prefer URL search template; DOM/vision paths are fallbacks.

HOST = "zakupki.rosatom.ru"

_SERVICE_HREF_RE = re.compile(
    r"/rss/|/export|mailto:|javascript:|#$|"
    r"news|contact|login|signin|кабинет",
    re.I,
)

_CARD_HREF_RE = re.compile(
    r"tender|procurement|purchase|notice|procedure|lot|"
    r"закупк|извещ|link=.*detail|id=\d+",
    re.I,
)

_ID_IN_TEXT_RE = re.compile(r"\b(\d{6,})\b")

_DEEP_COLLECT_JS = """() => {
  const out = [];
  const seen = new Set();
  const push = (href, text, tenderId, source) => {
    const h = String(href || '').trim();
    const t = String(text || '').replace(/\\s+/g, ' ').trim().slice(0, 160);
    if (!h && !tenderId) return;
    const key = h || ('id:' + tenderId);
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ href: h || null, text: t, tender_id: tenderId || null, source });
  };

  for (const a of document.querySelectorAll('a[href]')) {
    const raw = a.getAttribute('href');
    const href = String(raw == null ? '' : raw).trim();
    if (!href || href === '#' || href.startsWith('javascript:') || href.startsWith('mailto:')) continue;
    let abs = href;
    try { abs = new URL(href, location.href).href; } catch (e) { continue; }
    const text = a.innerText || a.textContent || a.getAttribute('title') || '';
    const tid = a.getAttribute('data-id')
      || a.getAttribute('data-tender-id')
      || a.getAttribute('data-procurement-id')
      || null;
    push(abs, text, tid, 'a');
    if (out.length >= 80) break;
  }

  // Clickable rows / SPA cards without classic href
  for (const el of document.querySelectorAll(
    'tr[data-id], tr[data-tender-id], [data-procurement-id], [data-tender-id], [data-uid], [role=\"row\"]'
  )) {
    const tid = el.getAttribute('data-id')
      || el.getAttribute('data-tender-id')
      || el.getAttribute('data-procurement-id')
      || el.getAttribute('data-uid')
      || null;
    const a = el.querySelector('a[href]');
    let href = a ? (a.href || a.getAttribute('href') || '') : '';
    if (href) {
      try { href = new URL(href, location.href).href; } catch (e) {}
    }
    const text = (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 160);
    push(href, text, tid, 'row');
    if (out.length >= 80) break;
  }

  // Buttons / divs with router-ish onclick
  for (const el of document.querySelectorAll('[onclick], [ng-click], [data-href]')) {
    const dataHref = el.getAttribute('data-href') || '';
    const onclick = String(el.getAttribute('onclick') || el.getAttribute('ng-click') || '');
    let href = dataHref;
    const m = onclick.match(/https?:[^'\"\\s]+|\\/[\\w\\-./?=&%#]+/);
    if (!href && m) href = m[0];
    if (href && href.startsWith('/')) {
      try { href = new URL(href, location.href).href; } catch (e) {}
    }
    const tid = el.getAttribute('data-id') || el.getAttribute('data-tender-id') || null;
    const text = (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120);
    if (href || tid) push(href, text, tid, 'onclick');
    if (out.length >= 80) break;
  }

  return { url: location.href, count: out.length, items: out };
}"""


def matches_url(url: str) -> bool:
    host = (urlparse(url or "").netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host == HOST or host.endswith("." + HOST)


def extract_tender_id(url: str, text: str | None = None) -> str | None:
    qs = parse_qs(urlparse(url or "").query)
    for key in (
        "id",
        "tenderId",
        "procurementId",
        "noticeId",
        "purchaseId",
        "number",
        "num",
    ):
        vals = qs.get(key) or []
        if vals and str(vals[0]).strip():
            return str(vals[0]).strip()
    path = urlparse(url or "").path or ""
    m = re.search(r"/(?:tender|procurement|purchase|notice|lot)/(\d{5,})", path, re.I)
    if m:
        return m.group(1)
    nums = re.findall(r"/(\d{6,})(?:/|$)", path)
    if nums:
        return nums[-1]
    if text:
        m2 = _ID_IN_TEXT_RE.search(text)
        if m2:
            return m2.group(1)
    return None


def is_service_href(url: str) -> bool:
    return bool(_SERVICE_HREF_RE.search(url or ""))


def is_card_candidate(url: str | None, text: str | None = None, tender_id: str | None = None) -> bool:
    if tender_id and str(tender_id).strip():
        if url and is_service_href(url):
            return False
        return True
    if not url:
        return False
    if is_service_href(url):
        return False
    if not matches_url(url) and not url.startswith("/"):
        # allow same-host only
        if "rosatom" not in (urlparse(url).netloc or "").lower():
            return False
    blob = f"{url} {text or ''}"
    return bool(_CARD_HREF_RE.search(blob))


def build_search_url(keywords: str) -> str:
    q = {
        "link": "procurements",
        "search": keywords or "",
    }
    return "https://zakupki.rosatom.ru/?" + urlencode(q, quote_via=quote)


async def _human_pause(rt: Any, seconds: float = 1.2) -> None:
    """Human-like pause (anti-bot / SPA paint)."""
    ms = int(max(0.3, float(seconds)) * 1000)
    try:
        await rt.page.wait_for_timeout(ms)
    except Exception:
        await asyncio.sleep(seconds)


class ZakupkiRosatomRuAdapter:
    host = HOST
    display_name = "Росатом (zakupki.rosatom.ru)"

    def matches(self, url: str) -> bool:
        return matches_url(url)

    def tender_id(self, url: str) -> str | None:
        return extract_tender_id(url)

    async def open_search(self, rt: Any, spec: SearchSpec) -> StepResult:
        """
        Prefer URL template (?link=procurements&search=) proven in full-tools log.
        Fallback: DOM fill + submit / vision locate.
        """
        url = build_search_url(spec.keywords)
        res = await navigate(rt, url)
        await _human_pause(rt, 2.0)
        kind_info = await detect_page_kind(rt)
        kind = str(kind_info.get("page_kind") or res.get("page_kind") or "unknown")
        page_url = rt.page.url or ""
        ok = bool(res.get("ok"))
        note = str(res.get("message") or "")

        # If blocked / empty SPA shell — try DOM search on procurements landing
        text_probe = ""
        try:
            text_probe = await rt.page.evaluate(
                "() => (document.body && document.body.innerText || '').slice(0, 400)"
            )
        except Exception:
            text_probe = ""
        blocked = bool(
            re.search(
                r"браузер устарел|доступ запрещен|waf|заблокир|captcha",
                text_probe or "",
                re.I,
            )
        )
        if blocked or (ok and len((text_probe or "").strip()) < 40):
            # DOM / vision path on base procurements page
            base = "https://zakupki.rosatom.ru/?link=procurements"
            await navigate(rt, base)
            await _human_pause(rt, 1.5)
            filled = await self._fill_search_ui(rt, spec.keywords)
            note = f"{note}; ui_fallback={filled.get('note')}"
            ok = bool(filled.get("ok")) or ok
            kind_info = await detect_page_kind(rt)
            kind = str(kind_info.get("page_kind") or kind)
            page_url = rt.page.url or page_url

        if "search=" in page_url or "procurements" in page_url:
            if kind in {"home", "unknown"}:
                kind = "search"

        return StepResult(
            ok=ok,
            page_kind=kind,
            url=page_url,
            note=note,
            data={
                "search_url": url,
                "keywords": spec.keywords,
                "blocked_hint": blocked,
            },
        )

    async def _fill_search_ui(self, rt: Any, keywords: str) -> dict[str, Any]:
        inputs = await browser_dom.query(rt, role="searchbox")
        if not inputs:
            inputs = await browser_dom.query(rt, placeholder="поиск")
        if not inputs:
            inputs = await browser_dom.query(rt, placeholder="search")
        if not inputs:
            inputs = await browser_dom.query(rt, text="поиск")
        if inputs:
            el = inputs[0]
            await browser_dom.fill_by_id(rt, int(el["id"]), keywords)
            btns = await browser_dom.query(rt, role="button", text="поиск")
            if not btns:
                btns = await browser_dom.query(rt, role="button", text="найти")
            if btns:
                await browser_dom.click_by_id(rt, int(btns[0]["id"]))
            else:
                await rt.page.keyboard.press("Enter")
            await _human_pause(rt, 2.0)
            return {"ok": True, "note": "dom_fill"}

        # Vision fallback when DOM empty (SPA / anti-bot) — always validate_point
        goal = (
            "Поле поиска закупок (input/searchbox с placeholder Поиск) "
            f"для ввода: {keywords}"
        )
        run_id = getattr(rt, "vision_run_id", None) or None
        gv = await ground_validated(
            rt,
            goal,
            run_id=run_id,
            platform=HOST,
        )
        if not gv.get("ok"):
            return {
                "ok": False,
                "note": f"vision_rejected:{gv.get('note')}:{gv.get('validation')}",
            }

        from ..core.browser.primitives import click_xy, type_text

        x, y = float(gv["x"]), float(gv["y"])
        await click_xy(rt, x, y)
        await type_text(rt, keywords, x=x, y=y)
        await rt.page.keyboard.press("Enter")
        await _human_pause(rt, 2.0)
        return {
            "ok": True,
            "note": (
                f"vision_validated:{gv.get('validation')}:"
                f"{gv.get('backend')}:{gv.get('note')}"
            ),
        }

    async def collect_cards(self, rt: Any) -> list[CardRef]:
        await _human_pause(rt, 0.8)
        try:
            raw = await rt.page.evaluate(_DEEP_COLLECT_JS)
        except Exception as e:
            logger.warning("rosatom collect_cards js failed: %s", e)
            raw = {"items": []}

        items = list((raw or {}).get("items") or [])
        out: list[CardRef] = []
        seen: set[str] = set()
        for item in items:
            href = (item.get("href") or "").strip() or None
            text = (item.get("text") or "").strip() or None
            tid = (item.get("tender_id") or None) or extract_tender_id(href or "", text)
            if not is_card_candidate(href, text, tid):
                continue
            key = href or f"id:{tid}"
            if key in seen:
                continue
            seen.add(key)
            # Synthetic URL if only id known — open_card will need click/DOM later
            url = href or f"https://zakupki.rosatom.ru/?link=procurements&id={tid}"
            out.append(
                CardRef(
                    url=url,
                    title=text,
                    tender_id=tid,
                    law="223",
                )
            )

        if not out:
            # Last resort: DOM links via query with tender-ish href
            links = await browser_dom.query(rt, role="link", href_re=r"tender|procurement|id=")
            for el in links:
                href = (el.get("href") or "").strip()
                if not href or not is_card_candidate(href, el.get("name")):
                    continue
                if href in seen:
                    continue
                seen.add(href)
                out.append(
                    CardRef(
                        url=href,
                        title=el.get("name"),
                        tender_id=extract_tender_id(href, el.get("name")),
                        law="223",
                    )
                )
        return out

    async def open_card(self, rt: Any, card: CardRef) -> StepResult:
        res = await navigate(rt, card.url)
        await _human_pause(rt, 1.5)
        kind_info = await detect_page_kind(rt)
        kind = str(kind_info.get("page_kind") or res.get("page_kind") or "unknown")
        if res.get("ok") and kind in {"home", "search", "unknown"}:
            # SPA detail often still looks like app shell
            kind = "card"
        tid = card.tender_id or extract_tender_id(rt.page.url or "", card.title)
        return StepResult(
            ok=bool(res.get("ok")),
            page_kind=kind,
            url=rt.page.url,
            note=str(res.get("message") or ""),
            data={"tender_id": tid, "law": "223", "card_url": card.url},
        )

    async def collect_documents(self, rt: Any) -> list[DocRef]:
        await _human_pause(rt, 1.0)
        raw = await list_download_links(rt, limit=40)
        docs: list[DocRef] = []
        seen: set[str] = set()
        for item in raw.get("links") or []:
            href = item.get("href")
            if not href:
                continue
            href = urljoin(rt.page.url, str(href))
            if href in seen or is_service_href(href):
                continue
            kind = str(item.get("kind") or "file")
            if kind == "noise":
                continue
            seen.add(href)
            docs.append(
                DocRef(
                    url=href,
                    name=str(item.get("text") or "document")[:160],
                    kind=kind,
                )
            )

        if not docs:
            # Deep scrape any file-like anchors
            try:
                items = await rt.page.evaluate(_DEEP_COLLECT_JS)
            except Exception:
                items = {"items": []}
            for item in (items or {}).get("items") or []:
                href = (item.get("href") or "").strip()
                text = (item.get("text") or "").strip()
                if not href:
                    continue
                if not re.search(r"download|file|doc|\.pdf|\.docx?|\.xlsx?|attach", f"{href} {text}", re.I):
                    continue
                if href in seen or is_service_href(href):
                    continue
                seen.add(href)
                docs.append(DocRef(url=href, name=text or "document", kind="file"))
        return docs

    async def next_page(self, rt: Any) -> bool:
        suggested = bump_page_url(rt.page.url or "")
        if suggested and suggested != rt.page.url:
            res = await navigate(rt, suggested)
            await _human_pause(rt, 1.5)
            return bool(res.get("ok"))
        nexts = await browser_dom.query(rt, role="link", text="след")
        if not nexts:
            nexts = await browser_dom.query(rt, role="button", text="след")
        if not nexts:
            nexts = await browser_dom.query(rt, role="link", text="next")
        if nexts:
            res = await browser_dom.click_by_id(rt, int(nexts[0]["id"]))
            await _human_pause(rt, 1.5)
            return bool(res.get("ok"))

        # Vision fallback for pagination — validate before click
        goal = "Кнопка следующей страницы пагинации (далее / Следующая / next / >)"
        run_id = getattr(rt, "vision_run_id", None) or None
        gv = await ground_validated(
            rt,
            goal,
            run_id=run_id,
            platform=HOST,
        )
        if not gv.get("ok"):
            return False
        from ..core.browser.primitives import click_xy

        await click_xy(rt, float(gv["x"]), float(gv["y"]))
        await _human_pause(rt, 1.5)
        return True
