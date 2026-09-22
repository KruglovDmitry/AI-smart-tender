"""Unit tests for filename encoding recovery."""

from __future__ import annotations

from app.core.browser.primitives import _fix_filename_encoding


def test_already_cyrillic() -> None:
    assert _fix_filename_encoding("НМЦК.xlsx") == "НМЦК.xlsx"


def test_utf8_mojibake() -> None:
    # UTF-8 bytes of «НМЦК» mis-decoded as latin-1
    mojibake = "НМЦК".encode("utf-8").decode("latin-1")
    assert _fix_filename_encoding(mojibake) == "НМЦК"


def test_empty() -> None:
    assert _fix_filename_encoding("") == ""
    assert _fix_filename_encoding("   ") == ""
