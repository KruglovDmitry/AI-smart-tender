from __future__ import annotations

import ipaddress
from urllib.parse import urljoin, urlparse

from ..config import Settings


def _host_allowed(host: str, settings: Settings) -> bool:
    if not settings.allowed_hosts:
        return True
    return any(host == item or host.endswith("." + item) for item in settings.allowed_hosts)


def validate_navigation_url(url: str, settings: Settings | None = None) -> tuple[bool, str]:
    """Публичный http(s). Локальные и внутренние адреса отклоняются."""
    settings = settings or Settings()
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False, "Разрешены только адреса http и https."
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return False, "В адресе нет хоста."
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return False, "Локальный хост запрещён."
    if host.isdigit():
        return False, "Числовой хост запрещён."
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip and (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return False, "Внутренний или локальный адрес запрещён."
    if not _host_allowed(host, settings):
        return False, f"Хост {host} нет в списке разрешённых."
    return True, ""


def same_site(url: str, task_host: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    root = (task_host or "").lower()
    return bool(host) and (host == root or host.endswith("." + root))


def task_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def absolute(base: str, href: str) -> str:
    return urljoin(base, href)
