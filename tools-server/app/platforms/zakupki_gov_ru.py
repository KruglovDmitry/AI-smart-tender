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


def documents_query_ok(url: str) -> bool:
    """
    True when documents.html query is usable.
    44-ФЗ: regNumber is enough.
    223 (notice223): need purchaseNoticeNumber and/or noticeGuid — regNumber alone → 404.
    """
    try:
        p = urlparse(url or "")
    except Exception:
        return False
    path = (p.path or "").lower()
    if "documents.html" not in path:
        return False
    qs = parse_qs(p.query or "")
    if "notice223" in path or "/223/" in path:
        return bool(qs.get("noticeGuid") or qs.get("purchaseNoticeNumber"))
    return bool(qs.get("regNumber") or qs.get("purchaseNoticeNumber") or qs.get("noticeGuid"))


def common_info_to_documents(url: str) -> str | None:
    """
    Derive documents URL from card common-info URL by replacing the last path segment.
    Keeps <TYPE> (zk20 / ea20 / …) and query string — avoids broken zkp20 shortcuts from listings.

    For notice223 with only ?regNumber= returns None (that URL 404s on EIS); caller must
    follow the «Документы» tab href (purchaseNoticeNumber + noticeGuid).
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
    candidate = urlunparse(
        (p.scheme, p.netloc, new_path, p.params, p.query, p.fragment)
    )
    if not documents_query_ok(candidate):
        return None
    return candidate


_FIND_DOCUMENTS_TAB_JS = """() => {
  const candidates = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const raw = a.getAttribute('href');
    const href = String(raw == null ? '' : raw).trim();
    if (!href || href.startsWith('javascript:') || href.startsWith('mailto:')) continue;
    let abs = href;
    try { abs = new URL(href, location.href).href; } catch (e) { continue; }
    if (!/documents\\.html/i.test(abs)) continue;
    if (/printForm|signview|listModal/i.test(abs)) continue;
    if (seen.has(abs)) continue;
    seen.add(abs);
    const text = String(a.innerText || a.textContent || a.getAttribute('title') || '')
      .replace(/\\s+/g, ' ').trim().slice(0, 80);
    candidates.push({ href: abs, text });
  }
  if (!candidates.length) return null;
  const preferred = candidates.find(c =>
    /noticeGuid=/i.test(c.href) || /purchaseNoticeNumber=/i.test(c.href)
  );
  return preferred || candidates[0];
}"""


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

    def search_url(self, spec: SearchSpec) -> str | None:
        return build_search_url(spec.keywords, spec.filters)

    def documents_url(self, card_url: str) -> str | None:
        from .. import config

        if getattr(config, "EIS_TEST_NO_DOCS_ROUTE", False):
            return None
        return common_info_to_documents(card_url)

    def next_page_url(self, url: str) -> str | None:
        suggested = bump_page_url(url or "")
        if suggested and suggested != url:
            return suggested
        return None

    async def open_search(self, rt: Any, spec: SearchSpec) -> StepResult:
        url = self.search_url(spec) or build_search_url(spec.keywords, spec.filters)
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

    async def _documents_tab_href(self, rt: Any) -> str | None:
        try:
            hit = await rt.page.evaluate(_FIND_DOCUMENTS_TAB_JS)
        except Exception:
            return None
        if not hit:
            return None
        href = str((hit or {}).get("href") or "").strip()
        return href or None

    async def collect_documents(self, rt: Any) -> list[DocRef]:
        """
        Open the documents tab, then scrape filestore file links.

        Prefer the «Документы» tab href from the card DOM (required for 223:
        purchaseNoticeNumber + noticeGuid). Fall back to path-replace only when
        the derived query is known-good (44-ФЗ regNumber).

        EIS_TEST_NO_DOCS_ROUTE=1: do not navigate via documents_url / tab helper;
        return only filestore links on the current page.
        """
        from .. import config

        current = rt.page.url or ""
        test_no_docs = bool(getattr(config, "EIS_TEST_NO_DOCS_ROUTE", False))
        already_ok = documents_query_ok(current)

        if not already_ok and not test_no_docs:
            docs_url = await self._documents_tab_href(rt)
            if not docs_url:
                docs_url = self.documents_url(current)
            if not docs_url:
                return []
            if docs_url.split("#")[0] != current.split("#")[0]:
                res = await navigate(rt, docs_url)
                if not res.get("ok"):
                    return []
            if not documents_query_ok(rt.page.url or ""):
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
