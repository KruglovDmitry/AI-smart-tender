"""Unit tests for EIS (zakupki.gov.ru) adapter URL rules — no network."""

from __future__ import annotations

from app.platforms.registry import get_adapter
from app.platforms.zakupki_gov_ru import (
    ZakupkiGovRuAdapter,
    build_search_url,
    common_info_to_documents,
    detect_law,
    extract_tender_id,
    is_card_href,
    is_service_href,
    matches_url,
)


def test_matches_host() -> None:
    assert matches_url("https://zakupki.gov.ru/epz/")
    assert matches_url("https://www.zakupki.gov.ru/")
    assert not matches_url("https://zakupki.rosatom.ru/")


def test_registry_picks_eis() -> None:
    ad = get_adapter("https://zakupki.gov.ru/epz/order/extendedsearch/results.html")
    assert isinstance(ad, ZakupkiGovRuAdapter)


def test_tender_id_reg_number() -> None:
    url = (
        "https://zakupki.gov.ru/epz/order/notice/zk20/view/common-info.html"
        "?regNumber=0829500001126006321"
    )
    assert extract_tender_id(url) == "0829500001126006321"


def test_tender_id_purchase_notice() -> None:
    url = (
        "https://zakupki.gov.ru/epz/order/notice/notice223/common-info.html"
        "?purchaseNoticeNumber=32616383976"
    )
    assert extract_tender_id(url) == "32616383976"


def test_detect_law_44_and_223() -> None:
    u44 = "https://zakupki.gov.ru/epz/order/notice/zk20/view/common-info.html?regNumber=1"
    u223 = "https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber=2"
    assert detect_law(u44) == "44"
    assert detect_law(u223) == "223"


def test_filter_service_links() -> None:
    assert is_service_href(
        "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString=x"
    )
    assert is_service_href(
        "https://zakupki.gov.ru/epz/order/notice/zk20/view/printForm/view.html?regNumber=1"
    )
    assert not is_card_href(
        "https://zakupki.gov.ru/epz/order/notice/zk20/view/documents.html?regNumber=1"
    )


def test_accept_common_info_card() -> None:
    url = (
        "https://zakupki.gov.ru/epz/order/notice/zk20/view/common-info.html"
        "?regNumber=0829500001126006321"
    )
    assert is_card_href(url)


def test_common_info_to_documents_keeps_type() -> None:
    # Broken listing shortcut would be zkp20 — adapter must keep zk20 from card URL
    card = (
        "https://zakupki.gov.ru/epz/order/notice/zk20/view/common-info.html"
        "?regNumber=0829500001126006321"
    )
    docs = common_info_to_documents(card)
    assert docs is not None
    assert "/notice/zk20/view/documents.html" in docs
    assert "regNumber=0829500001126006321" in docs
    assert "zkp20" not in docs


def test_common_info_to_documents_223() -> None:
    card = (
        "https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html"
        "?regNumber=32616383976"
    )
    docs = common_info_to_documents(card)
    assert docs is not None
    assert docs.endswith("documents.html?regNumber=32616383976") or (
        "documents.html" in docs and "regNumber=32616383976" in docs
    )
    assert "common-info" not in docs


def test_build_search_url() -> None:
    url = build_search_url("канцтовары", {"fz44": True, "fz223": True})
    assert "extendedsearch/results.html" in url
    assert "searchString=" in url
    assert "fz44=on" in url
    assert "fz223=on" in url
