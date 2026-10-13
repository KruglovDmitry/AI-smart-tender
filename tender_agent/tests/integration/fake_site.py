from __future__ import annotations

from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"

_PAGES = {
    "/": "home.html",
    "/search": "search.html",
    "/spa": "dynamic_list.html",
    "/empty": "empty.html",
    "/missing": "error.html",
    "/login": "login.html",
    "/captcha": "captcha.html",
    "/unknown": "unknown.html",
    "/noop": "noop.html",
    "/ambiguous": "ambiguous.html",
    "/mixed": "mixed.html",
    "/trap": "trap.html",
    "/plain": "plain.html",
    "/downloads": "downloads.html",
}


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def resolve(url: str) -> tuple[int, dict[str, str], bytes]:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    html = {"content-type": "text/html; charset=utf-8"}
    if path == "/files/spec.pdf":
        return 200, {
            "content-type": "application/pdf",
            "content-disposition": 'attachment; filename="spec.pdf"',
        }, PDF
    if path == "/tenders":
        page = (parse_qs(parsed.query).get("page") or ["1"])[0]
        name = "pagination.html" if page == "2" else "tender_list.html"
        query = escape((parse_qs(parsed.query).get("q") or [""])[0], quote=True)
        body = _read(name).replace(b"__Q__", query.encode("utf-8"))
        return 200, html, body
    if path.startswith("/tenders/"):
        tender_id = path.rsplit("/", 1)[-1]
        body = _read("tender_details.html").replace(b"__ID__", tender_id.encode("utf-8"))
        return 200, html, body
    if path in _PAGES:
        return 200, html, _read(_PAGES[path])
    return 404, html, _read("error.html")


async def fulfill(route) -> None:
    host = (urlparse(route.request.url).hostname or "").lower()
    if host != "tenders.example.test":
        await route.fallback()
        return
    status, headers, body = resolve(route.request.url)
    await route.fulfill(status=status, headers=headers, body=body)
