"""Host → platform adapter registry."""

from __future__ import annotations

from urllib.parse import urlparse

from .base import GenericAdapter, PlatformAdapter, host_of
from .zakupki_gov_ru import ZakupkiGovRuAdapter

_ADAPTERS: list[PlatformAdapter] = [
    ZakupkiGovRuAdapter(),
]
_GENERIC = GenericAdapter()


def register(adapter: PlatformAdapter) -> None:
    """Register or replace adapter for its host (test/extension hook)."""
    global _ADAPTERS
    host = getattr(adapter, "host", "").lower()
    _ADAPTERS = [a for a in _ADAPTERS if getattr(a, "host", "").lower() != host]
    _ADAPTERS.insert(0, adapter)


def get_adapter(url: str) -> PlatformAdapter:
    """Return the best adapter for URL host; fallback → GenericAdapter."""
    for adapter in _ADAPTERS:
        if adapter.matches(url):
            return adapter
    # also try by bare host equality
    host = host_of(url)
    for adapter in _ADAPTERS:
        if getattr(adapter, "host", "") == host:
            return adapter
    return _GENERIC


def list_adapters() -> list[PlatformAdapter]:
    return list(_ADAPTERS) + [_GENERIC]


def platform_host(url: str) -> str:
    return host_of(url) or (urlparse(url or "").netloc or "unknown")
