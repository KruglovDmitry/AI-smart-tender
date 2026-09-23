"""Unit tests for resolve_tender_id."""

from __future__ import annotations

from app.domain.tender_id import resolve_tender_id


def test_reg_number_query() -> None:
    info = resolve_tender_id(
        "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=0123456789012345678"
    )
    assert info["tender_id"] == "0123456789012345678"
    assert info["method"] == "query:regNumber"


def test_purchase_notice_number() -> None:
    info = resolve_tender_id(
        "https://zakupki.gov.ru/epz/order/notice/notice223/common-info.html"
        "?purchaseNoticeNumber=32616383976&noticeGuid=abc"
    )
    assert info["tender_id"] == "32616383976"
    assert info["method"] == "query:purchaseNoticeNumber"


def test_path_numeric_segment() -> None:
    info = resolve_tender_id("https://example.com/tender/12345678/docs")
    assert info["tender_id"] == "12345678"
    assert info["method"].startswith("path:")


def test_hash_fallback() -> None:
    info = resolve_tender_id("https://example.com/about")
    assert info["method"] == "hash:url"
    assert len(info["tender_id"]) == 16
