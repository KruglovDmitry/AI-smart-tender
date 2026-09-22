"""Unit tests for download_result_meta."""

from __future__ import annotations

import hashlib

from app.core.browser.primitives import download_result_meta


def test_meta_fields() -> None:
    body = b"hello-bytes"
    meta = download_result_meta(body, "application/pdf; charset=utf-8")
    assert meta["bytes"] == len(body)
    assert meta["sha256"] == hashlib.sha256(body).hexdigest()
    assert meta["content_type"] == "application/pdf"


def test_empty_content_type() -> None:
    meta = download_result_meta(b"x", "")
    assert meta["content_type"] == ""
    assert meta["bytes"] == 1
