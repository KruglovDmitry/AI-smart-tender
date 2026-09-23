"""Unit tests for manifest.json contract."""

from __future__ import annotations

from pathlib import Path

from app.domain.manifest import (
    append_file,
    empty_manifest,
    load_manifest,
    validate_manifest,
)


def test_manifest_roundtrip(tmp_path: Path) -> None:
    data = empty_manifest(
        tender_id="123",
        platform="zakupki.gov.ru",
        tender_url="https://zakupki.gov.ru/x",
    )
    assert validate_manifest(data) == []
    append_file(
        tmp_path,
        name="a.pdf",
        sha256="abc",
        bytes_count=10,
        source_url="https://zakupki.gov.ru/file",
        content_type="application/pdf",
        tender_id="123",
        platform="zakupki.gov.ru",
        tender_url="https://zakupki.gov.ru/x",
    )
    loaded = load_manifest(tmp_path)
    assert loaded is not None
    assert loaded["files"][0]["name"] == "a.pdf"
    assert validate_manifest(loaded) == []


def test_validate_missing_fields() -> None:
    errs = validate_manifest({"tender_id": "1"})
    assert "missing platform" in errs
