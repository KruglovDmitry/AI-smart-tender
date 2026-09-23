"""EIS (zakupki.gov.ru) platform adapter — URL templates + 44/223 document routing."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse, urlunparse

from ..core.browser.page_kind import detect_page_kind
from ..core.browser.primitives import navigate
from ..core.browser.url_utils import bump_page_url
from .base import CardRef, DocRef, SearchSpec, StepResult

HOST = "zakupki.gov.ru"

# Service / non-card paths on results pages
_SERVICE_PATH_RE = re.compile(
    r"extendedsearch/results\.html|"
    r"/orderplan/|"
    r"/orderclause/|"
    r"/typalclause/|"
    r"/printForm/|"
    r"listModal\.html|"
    r"/signview/|"
    r"/document/view\.html",
    re.I,
)

# Real purchase cards: …/common-info.html?regNumber=… (44) or purchaseNoticeNumber (223)
_CARD_COMMON_INFO_RE = re.compile(
    r"common-info\.html\?[^#]*(?:regNumber|purchaseNoticeNumber)=",
    re.I,
)

_FILESTORE_RE = re.compile(
    r"filestore/.*/file\.html\?[^#]*uid=",
    re.I,
)


def matches_url(url: str) -> bool:
    host = (urlparse(url or "").netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host == HOST or host.endswith("." + HOST)


def extract_tender_id(url: str) -> str | None:
    qs = parse_qs(urlparse(url or "").query)
    for key in ("regNumber", "purchaseNoticeNumber"):
        vals = qs.get(key) or []
        if vals and str(vals[0]).strip():
            return str(vals[0]).strip()
    return None


def detect_law(url: str) -> str | None:
    """44 if /epz/order/notice/<TYPE>/…; 223 if /223/purchase/…"""
    path = (urlparse(url or "").path or "").lower()
    if "/223/" in path or "notice223" in path:
        return "223"
    if "/epz/order/notice/" in path:
        return "44"
    return None


def is_service_href(url: str) -> bool:
    return bool(_SERVICE_PATH_RE.search(url or ""))


def is_card_href(url: str) -> bool:
    """Accept only common-info cards with regNumber; reject service links."""
    if not url or is_service_href(url):
        return False
    if not matches_url(url):
        return False
    return bool(_CARD_COMMON_INFO_RE.search(url))


def common_info_to_documents(url: str) -> str | None:
    """
    Derive documents URL from card common-info URL by replacing the last path segment.
    Keeps <TYPE> (zk20 / ea20 / …) and query string — avoids broken zkp20 shortcuts from listings.
    """
    try:
        p = urlparse(url or "")
    except Exception:
        return None
    path = p.path or ""
    if "common-info.html" not in path.lower():
        return None
    # replace trailing common-info.html (case-insensitive)
    new_path = re.sub(
        r"common-info\.html\s*$",
        "documents.html",
        path,
        count=1,
        flags=re.I,
    )
    if new_path == path:
        return None
    return urlunparse((p.scheme, p.netloc, new_path, p.params, p.query, p.fragment))


def build_search_url(keywords: str, filters: dict[str, Any] | None = None) -> str:
    q: dict[str, str] = {"searchString": keywords or ""}
    filters = filters or {}
    # optional law toggles etc.
    for k, v in filters.items():
        if v is None or v is False:
            continue
        if v is True:
            q[str(k)] = "on"
        else:
            q[str(k)] = str(v)
    return (
        "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?"
        + urlencode(q, quote_via=quote)
    )


_COLLECT_HREFS_JS = """() => {
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const raw = a.getAttribute('href');
    const href = String(raw == null ? '' : raw).trim();
    if (!href || href.startsWith('javascript:') || href.startsWith('mailto:')) continue;
    let abs = href;
    try { abs = new URL(href, location.href).href; } catch (e) { continue; }
    if (!/^https?:/i.test(abs)) continue;
    if (seen.has(abs)) continue;
    seen.add(abs);
    const text = String(a.innerText || a.textContent || a.getAttribute('title') || '')
      .replace(/\\s+/g, ' ').trim().slice(0, 160);
    out.push({ href: abs, text });
    if (out.length >= 80) break;
  }
  return out;
}"""


class ZakupkiGovRuAdapter:
    host = HOST
    display_name = "ЕИС (zakupki.gov.ru)"

    def matches(self, url: str) -> bool:
        return matches_url(url)

    def tender_id(self, url: str) -> str | None:
        return extract_tender_id(url)

    async def open_search(self, rt: Any, spec: SearchSpec) -> StepResult:
        url = build_search_url(spec.keywords, spec.filters)
        res = await navigate(rt, url)
        kind_info = await detect_page_kind(rt)
        kind = str(kind_info.get("page_kind") or res.get("page_kind") or "unknown")
        ok = bool(res.get("ok")) and kind in {"search", "unknown"}
        # even if kind mis-detects, URL template success is enough when navigate ok
        if res.get("ok") and "results.html" in (rt.page.url or ""):
            ok = True
            kind = "search"
        return StepResult(
            ok=ok,
            page_kind=kind,
            url=rt.page.url,
            note=str(res.get("message") or ""),
            data={"search_url": url, "keywords": spec.keywords},
        )

    async def collect_cards(self, rt: Any) -> list[CardRef]:
        try:
            raw = await rt.page.evaluate(_COLLECT_HREFS_JS)
        except Exception:
            raw = []
        out: list[CardRef] = []
        seen: set[str] = set()
        for item in raw or []:
            href = str((item or {}).get("href") or "").strip()
            if not href or href in seen:
                continue
            if not is_card_href(href):
                continue
            seen.add(href)
            out.append(
                CardRef(
                    url=href,
                    title=str((item or {}).get("text") or "")[:160] or None,
                    tender_id=extract_tender_id(href),
                    law=detect_law(href),
                )
            )
        return out

    async def open_card(self, rt: Any, card: CardRef) -> StepResult:
        res = await navigate(rt, card.url)
        kind_info = await detect_page_kind(rt)
        kind = str(kind_info.get("page_kind") or res.get("page_kind") or "unknown")
        if res.get("ok") and ("common-info" in (rt.page.url or "").lower() or kind == "card"):
            kind = "card"
        tid = card.tender_id or extract_tender_id(card.url) or extract_tender_id(rt.page.url)
        return StepResult(
            ok=bool(res.get("ok")),
            page_kind=kind,
            url=rt.page.url,
            note=str(res.get("message") or ""),
            data={
                "tender_id": tid,
                "law": card.law or detect_law(rt.page.url),
                "card_url": card.url,
            },
        )

    async def collect_documents(self, rt: Any) -> list[DocRef]:
        """
        Navigate to documents.html derived from current/card common-info URL,
        then scrape filestore file links. Never trust listing documents.html shortcuts.
        """
        current = rt.page.url or ""
        docs_url = common_info_to_documents(current)
        if docs_url is None and "documents.html" not in current.lower():
            # try from data if caller left us on wrong page — no card url available here
            return []
        if docs_url and docs_url.split("#")[0] != current.split("#")[0]:
            res = await navigate(rt, docs_url)
            if not res.get("ok"):
                return []

        try:
            raw = await rt.page.evaluate(_COLLECT_HREFS_JS)
        except Exception:
            raw = []

        docs: list[DocRef] = []
        seen: set[str] = set()
        base = rt.page.url
        for item in raw or []:
            href = str((item or {}).get("href") or "").strip()
            text = str((item or {}).get("text") or "").strip()
            if not href:
                continue
            href = urljoin(base, href)
            if href in seen:
                continue
            if not _FILESTORE_RE.search(href):
                continue
            seen.add(href)
            docs.append(
                DocRef(
                    url=href,
                    name=(text or "document")[:160],
                    kind="file",
                )
            )
        return docs

    async def next_page(self, rt: Any) -> bool:
        suggested = bump_page_url(rt.page.url or "")
        if not suggested or suggested == rt.page.url:
            return False
        res = await navigate(rt, suggested)
        return bool(res.get("ok"))
