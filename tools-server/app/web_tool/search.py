"""Поиск в интернете: DuckDuckGo без ключа или Brave, если в дашборде задан ключ."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from ..desk_settings import text

_MAX = 10
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


def _provider() -> tuple[str, str]:
    provider = text("search_provider", "duckduckgo", ("SEARCH_PROVIDER",)).lower()
    key = text("search_api_key", env=("SEARCH_API_KEY", "BRAVE_SEARCH_API_KEY"))
    if provider not in {"duckduckgo", "brave"}:
        provider = "duckduckgo"
    return provider, key


def _link(href: str) -> str:
    parsed = urlparse(href)
    if parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target.startswith("http"):
            return target
    return href if href.startswith("http") else ""


def parse_duckduckgo(html: str, limit: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for link in soup.select("a.result__a"):
        url = _link(link.get("href") or "")
        title = link.get_text(" ", strip=True)
        if not url or not title:
            continue
        block = link.find_parent(class_="result")
        snippet = ""
        if block is not None:
            node = block.select_one(".result__snippet")
            snippet = node.get_text(" ", strip=True) if node else ""
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= limit:
            break
    return results


def _duckduckgo(query: str, limit: int) -> list[dict]:
    response = httpx.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": _UA},
        timeout=20,
        follow_redirects=True,
    )
    response.raise_for_status()
    return parse_duckduckgo(response.text, limit)


def _brave(query: str, limit: int, key: str) -> list[dict]:
    response = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": limit},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        timeout=20,
    )
    response.raise_for_status()
    results = []
    for item in (response.json().get("web") or {}).get("results") or []:
        url = str(item.get("url") or "")
        title = str(item.get("title") or "")
        if not url or not title:
            continue
        results.append({"title": title, "url": url, "snippet": str(item.get("description") or "")})
        if len(results) >= limit:
            break
    return results


def search_web(query: str, limit: int = 5) -> dict:
    text_query = (query or "").strip()
    if not text_query:
        raise ValueError("Пустой поисковый запрос.")
    if len(text_query) > 300:
        raise ValueError("Слишком длинный запрос.")
    limit = max(1, min(int(limit), _MAX))
    provider, key = _provider()
    if provider == "brave":
        if not key:
            raise ValueError("Для Brave укажите ключ поиска во вкладке «Агент» дашборда.")
        results = _brave(text_query, limit, key)
    else:
        provider = "duckduckgo"
        results = _duckduckgo(text_query, limit)
    return {"provider": provider, "query": text_query, "results": results}


def check_search() -> dict:
    found = search_web("example", limit=1)
    return {"ok": True, "detail": f"Поиск ({found['provider']}) отвечает, результатов: {len(found['results'])}."}
