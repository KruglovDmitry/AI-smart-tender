"""Platform adapter Protocol, dataclasses, and GenericAdapter (DOM heuristics)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlparse

from ..core.browser import dom as browser_dom
from ..core.browser.page_kind import detect_page_kind
from ..core.browser.primitives import list_download_links, navigate
from ..core.browser.url_utils import bump_page_url


@dataclass
class CardRef:
    url: str
    title: str | None = None
    tender_id: str | None = None
    law: str | None = None


@dataclass
class DocRef:
    url: str
    name: str
    kind: str = "file"


@dataclass
class SearchSpec:
    keywords: str
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    ok: bool
    page_kind: str
    url: str
    note: str = ""
    data: dict[str, Any] | None = None


@runtime_checkable
class PlatformAdapter(Protocol):
    host: str
    display_name: str

    def matches(self, url: str) -> bool: ...

    def tender_id(self, url: str) -> str | None: ...

    async def open_search(self, rt: Any, spec: SearchSpec) -> StepResult: ...

    async def collect_cards(self, rt: Any) -> list[CardRef]: ...

    async def open_card(self, rt: Any, card: CardRef) -> StepResult: ...

    async def collect_documents(self, rt: Any) -> list[DocRef]: ...

    async def next_page(self, rt: Any) -> bool: ...


def host_of(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


class GenericAdapter:
    """Fallback adapter: DOM heuristics for unknown hosts."""

    host = "*"
    display_name = "generic"

    def matches(self, url: str) -> bool:
        return True

    def tender_id(self, url: str) -> str | None:
        qs = parse_qs(urlparse(url or "").query)
        for key in ("id", "tenderId", "tradeId", "procedureId", "lotId", "regNumber"):
            vals = qs.get(key) or []
            if vals and str(vals[0]).strip():
                return str(vals[0]).strip()
        m = re.search(r"/(\d{5,})(?:/|$)", urlparse(url or "").path)
        return m.group(1) if m else None

    async def open_search(self, rt: Any, spec: SearchSpec) -> StepResult:
        # Try DOM: find search field, fill, submit
        inputs = await browser_dom.query(
            rt, role="searchbox"
        ) or await browser_dom.query(rt, placeholder="поиск") or await browser_dom.query(
            rt, placeholder="search"
        )
        if not inputs:
            inputs = await browser_dom.query(rt, text="найти")
        if inputs and spec.keywords:
            el = inputs[0]
            await browser_dom.fill_by_id(rt, int(el["id"]), spec.keywords)
            # prefer submit button
            btns = await browser_dom.query(rt, role="button", text="найти")
            if not btns:
                btns = await browser_dom.query(rt, role="button", text="поиск")
            if btns:
                await browser_dom.click_by_id(rt, int(btns[0]["id"]))
            else:
                await rt.page.keyboard.press("Enter")
            await rt.page.wait_for_timeout(800)
        kind = await detect_page_kind(rt)
        return StepResult(
            ok=True,
            page_kind=str(kind.get("page_kind") or "unknown"),
            url=rt.page.url,
            note="generic DOM search",
            data={"keywords": spec.keywords},
        )

    async def collect_cards(self, rt: Any) -> list[CardRef]:
        links = await browser_dom.query(
            rt, role="link", href_re=r"tender|purchase|notice|lot|procedure|trade"
        )
        out: list[CardRef] = []
        seen: set[str] = set()
        for el in links:
            href = (el.get("href") or "").strip()
            if not href or href in seen:
                continue
            seen.add(href)
            out.append(
                CardRef(
                    url=href,
                    title=(el.get("name") or None),
                    tender_id=self.tender_id(href),
                )
            )
        return out

    async def open_card(self, rt: Any, card: CardRef) -> StepResult:
        res = await navigate(rt, card.url)
        kind = str(res.get("page_kind") or "")
        return StepResult(
            ok=bool(res.get("ok")),
            page_kind=kind or "unknown",
            url=str(res.get("url") or rt.page.url),
            note=str(res.get("message") or ""),
            data={"tender_id": card.tender_id or self.tender_id(card.url)},
        )

    async def collect_documents(self, rt: Any) -> list[DocRef]:
        raw = await list_download_links(rt, limit=40)
        docs: list[DocRef] = []
        for item in raw.get("links") or []:
            href = item.get("href")
            if not href:
                continue
            kind = str(item.get("kind") or "file")
            if kind == "noise":
                continue
            docs.append(
                DocRef(
                    url=str(href),
                    name=str(item.get("text") or "document")[:160],
                    kind=kind,
                )
            )
        return docs

    async def next_page(self, rt: Any) -> bool:
        suggested = bump_page_url(rt.page.url or "")
        if suggested and suggested != rt.page.url:
            res = await navigate(rt, suggested)
            return bool(res.get("ok"))
        nexts = await browser_dom.query(rt, role="link", text="след")
        if not nexts:
            nexts = await browser_dom.query(rt, role="button", text="след")
        if not nexts:
            nexts = await browser_dom.query(rt, role="link", text="next")
        if nexts:
            res = await browser_dom.click_by_id(rt, int(nexts[0]["id"]))
            return bool(res.get("ok"))
        return False
