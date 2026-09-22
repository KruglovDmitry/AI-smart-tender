"""URL helpers for pagination hints (platform-agnostic)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def bump_page_url(url: str) -> str | None:
    """If URL has a numeric page-like query param, return URL with +1."""
    try:
        p = urlparse(url)
        qs = parse_qs(p.query, keep_blank_values=True)
    except Exception:
        return None
    for key in ("pageNumber", "page", "p", "PAGEN_1", "offset"):
        if key not in qs and key.lower() not in {k.lower() for k in qs}:
            continue
        # find actual key casing
        real = next(k for k in qs if k.lower() == key.lower())
        raw = (qs.get(real) or ["1"])[0] or "1"
        try:
            n = int(str(raw))
        except ValueError:
            continue
        step = 10 if real.lower() == "offset" else 1
        qs[real] = [str(n + step)]
        new_q = urlencode({k: v[0] if len(v) == 1 else v for k, v in qs.items()}, doseq=True)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, p.fragment))
    # no page param — suggest adding pageNumber=2 as soft hint only if looks like results
    low = url.lower()
    if any(x in low for x in ("result", "search", "query", "find")):
        qs = parse_qs(p.query, keep_blank_values=True)
        if "pageNumber" not in qs and "page" not in qs:
            qs["pageNumber"] = ["2"]
            new_q = urlencode({k: v[0] if len(v) == 1 else v for k, v in qs.items()}, doseq=True)
            return urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, p.fragment))
    return None


# Back-compat alias used by older callers/tests
_bump_page_url = bump_page_url
