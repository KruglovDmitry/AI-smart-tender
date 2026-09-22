"""Unit tests for extension guessing from bytes."""

from __future__ import annotations

import io
import zipfile

from app.core.browser.primitives import _guess_ext_from_bytes


def test_pdf_magic() -> None:
    assert _guess_ext_from_bytes(b"%PDF-1.4 fake") == ".pdf"


def test_docx_zip() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<w/>")
    assert _guess_ext_from_bytes(buf.getvalue()) == ".docx"


def test_xlsx_zip() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", "<x/>")
    assert _guess_ext_from_bytes(buf.getvalue()) == ".xlsx"


def test_ole_doc() -> None:
    body = b"\xd0\xcf\x11\xe0" + b"\x00" * 100 + b"WordDocument"
    assert _guess_ext_from_bytes(body) == ".doc"


def test_unknown() -> None:
    assert _guess_ext_from_bytes(b"hello") == ""
