"""Просмотр внешней страницы — как browse в ChatGPT / DeepSeek chat."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
import trafilatura
from trafilatura.settings import use_config


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are allowed")
    if not parsed.netloc:
        raise ValueError("Invalid URL")
    # block obvious local/metadata targets
    host = parsed.hostname or ""
    blocked = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "metadata.google.internal",
    }
    if host in blocked or host.startswith("169.254."):
        raise ValueError("Local/metadata URLs are not allowed")
    return url


def fetch_page(url: str, max_chars: int, timeout_sec: float) -> dict[str, Any]:
    """
    Скачать страницу и извлечь основной читаемый текст
    (title + article body), без меню/футера — ближе к web-browsing чатов.
    """
    url = _validate_url(url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; TenderTools/1.0; +https://localhost) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    with httpx.Client(
        follow_redirects=True,
        timeout=timeout_sec,
        headers=headers,
    ) as client:
        try:
            resp = client.get(url)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Soft failure: keep HTTP 200 from the tool so the model can retry
            # with the user's original URL instead of inventing paths.
            status = e.response.status_code
            final = str(e.response.url)
            return {
                "ok": False,
                "url": url,
                "final_url": final,
                "title": None,
                "content_type": e.response.headers.get("content-type", ""),
                "content": (
                    f"Fetch failed with HTTP {status} for {final}. "
                    "Retry with the exact URL the user provided; "
                    "do not invent alternate paths."
                ),
                "chars": 0,
                "truncated": False,
                "extractor": "http-error",
                "http_status": status,
            }
        content_type = resp.headers.get("content-type", "")
        html = resp.text
        final_url = str(resp.url)

    # PDF по ссылке — вернём пометку (полноценный PDF-парсинг URL можно расширить)
    if "application/pdf" in content_type.lower() or final_url.lower().endswith(".pdf"):
        return {
            "url": url,
            "final_url": final_url,
            "title": None,
            "content_type": content_type,
            "content": (
                "The URL points to a PDF. Download it to the server data folder "
                "and use read_document instead, or attach the PDF in chat."
            ),
            "chars": 0,
            "truncated": False,
            "extractor": "pdf-link-note",
        }

    cfg = use_config()
    cfg.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")

    extracted = trafilatura.extract(
        html,
        url=final_url,
        include_comments=False,
        include_tables=True,
        include_links=True,
        output_format="txt",
        favor_recall=True,
        config=cfg,
    )
    meta = trafilatura.extract_metadata(html, default_url=final_url)
    title = meta.title if meta else None

    if not extracted:
        # fallback: грубый plaintext
        extracted = trafilatura.html2txt(html) if html else ""

    truncated = False
    if max_chars > 0 and extracted and len(extracted) > max_chars:
        extracted = extracted[:max_chars] + "\n\n[... truncated ...]"
        truncated = True

    return {
        "url": url,
        "final_url": final_url,
        "title": title,
        "content_type": content_type,
        "content": extracted or "",
        "chars": len(extracted or ""),
        "truncated": truncated,
        "extractor": "trafilatura",
        "note": (
            "Page fetched and cleaned like browser web-chat browsing "
            "(main content, tables; chrome/nav stripped)."
        ),
    }
