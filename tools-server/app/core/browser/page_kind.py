"""Heuristic page classification — platform-agnostic signals only."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from .session import BrowserRuntime

_NOT_FOUND = re.compile(
    r"не найдена|не найден|page not found|\b404\b|not found|ошибка 404",
    re.I,
)
_LOGIN = re.compile(r"\blogin\b|sign[\s-]?in|авторизац|вход в систем|log in", re.I)
_CAPTCHA = re.compile(r"captcha|капча|recaptcha|hcaptcha|я не робот", re.I)
_DOCS = re.compile(r"document|/docs?\b|вложен|attach|файл", re.I)
_SEARCH = re.compile(
    r"result|search|выдач|найден[оа]|searchstring|query=|q=|pageNumber|page=",
    re.I,
)
# Generic card signals only — no site-specific query keys / EIS path segments.
_CARD = re.compile(
    r"/notice/|/purchase/|/tender/|/lot/|/procedure/|/trade/|"
    r"\bnotice\b|\bpurchase\b|\btender\b|\blot\b",
    re.I,
)


def classify_page_kind(
    url: str,
    title: str = "",
    body: str = "",
) -> dict[str, Any]:
    """
    Pure classification from URL/title/body text.
    Kinds: not_found | login | captcha | documents | search | card | home | unknown
    """
    url = url or ""
    title = title or ""
    body = body or ""
    blob = f"{url}\n{title}\n{body}"

    kind = "unknown"
    reason = ""
    if _NOT_FOUND.search(title) or _NOT_FOUND.search(body):
        kind = "not_found"
        reason = "title/body looks like not found"
    elif _CAPTCHA.search(blob):
        kind = "captcha"
        reason = "captcha markers"
    elif _LOGIN.search(blob) and ("password" in body.lower() or "парол" in body.lower()):
        kind = "login"
        reason = "login form markers"
    elif _DOCS.search(url) and not _SEARCH.search(url):
        kind = "documents"
        reason = "documents-like URL"
    elif _SEARCH.search(url) or _SEARCH.search(title):
        kind = "search"
        reason = "search/results markers in URL/title"
    elif _CARD.search(url):
        kind = "card"
        reason = "card-like URL"
    elif urlparse(url).path in {"", "/"} or re.search(r"home|index|main", url, re.I):
        kind = "home"
        reason = "home/landing-like URL"
    else:
        kind = "unknown"
        reason = "no strong signal"

    qs = parse_qs(urlparse(url).query)
    page_params = {
        k: (v[0] if v else "")
        for k, v in qs.items()
        if k.lower() in {"page", "pagenumber", "p", "offset", "start"}
    }

    return {
        "page_kind": kind,
        "page_kind_reason": reason,
        "url": url,
        "title": title,
        "http_status_hint": None,
        "page_query": page_params or None,
    }


async def detect_page_kind(rt: BrowserRuntime) -> dict[str, Any]:
    """Return page_kind + lightweight signals for the agent (reads live page)."""
    page = rt.page
    url = page.url or ""
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        body = await page.evaluate(
            """() => (document.body && (document.body.innerText || ''))
              .replace(/\\s+/g, ' ').trim().slice(0, 800)"""
        )
    except Exception:
        body = ""
    return classify_page_kind(url, title, body or "")
