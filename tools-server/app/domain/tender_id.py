"""Heuristics to extract stable tender IDs from URLs (v0 platforms)."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlparse


def platform_from_url(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "unknown"


def resolve_tender_id(url: str, platform: str | None = None) -> dict[str, str]:
    """
    Return tender_id and method used.
    Falls back to sha256(url)[:16] when no stable id found.
    """
    url = (url or "").strip()
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    platform = platform or platform_from_url(url)

    for key in (
        "regNumber",
        "purchaseNoticeNumber",
        "noticeInfoId",
        "id",
        "tradeId",
        "procedureId",
        "lotId",
    ):
        vals = qs.get(key) or []
        if vals and str(vals[0]).strip():
            return {"tender_id": str(vals[0]).strip(), "method": f"query:{key}"}

    path_patterns = [
        r"/epz/order/notice/[^/]+/[^/]+\.html",
        r"/trade/(?:view/)?(\d+)",
        r"/procedure/(\d+)",
        r"/lot/(\d+)",
        r"/tender/(\d+)",
        r"/purchase/(\d+)",
        r"/notice/(\d+)",
        r"/auctions/(\d+)",
    ]
    for pat in path_patterns:
        m = re.search(pat, parsed.path, re.I)
        if m:
            if m.lastindex:
                return {"tender_id": m.group(1), "method": f"path:{pat}"}
            break

    nums = re.findall(r"/(\d{5,})", parsed.path)
    if nums:
        return {"tender_id": nums[-1], "method": "path:numeric_segment"}

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return {"tender_id": digest, "method": "hash:url"}
