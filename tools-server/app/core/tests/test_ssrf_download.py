"""Unit tests for download SSRF allowlist."""

from __future__ import annotations

from types import SimpleNamespace

from app.core.browser.primitives import _host_allowed_for_download


def test_same_host_allowed() -> None:
    rt = SimpleNamespace(page=SimpleNamespace(url="https://zakupki.gov.ru/epz/"))
    assert _host_allowed_for_download(
        rt, "https://zakupki.gov.ru/filestore/public/1.0/download/file.html?uid=1"
    )


def test_foreign_host_blocked() -> None:
    rt = SimpleNamespace(page=SimpleNamespace(url="https://zakupki.gov.ru/epz/"))
    assert not _host_allowed_for_download(rt, "https://evil.example/steal")
