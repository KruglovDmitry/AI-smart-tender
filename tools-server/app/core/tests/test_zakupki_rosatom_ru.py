"""Unit tests for Rosatom adapter URL / filter rules — no network."""

from __future__ import annotations

from app.platforms.registry import get_adapter
from app.platforms.zakupki_rosatom_ru import (
    ZakupkiRosatomRuAdapter,
    build_search_url,
    extract_tender_id,
    is_card_candidate,
    is_service_href,
    matches_url,
)


def test_matches_and_registry() -> None:
    assert matches_url("https://zakupki.rosatom.ru/?link=procurements")
    assert not matches_url("https://zakupki.gov.ru/")
    ad = get_adapter("https://zakupki.rosatom.ru/?link=procurements")
    assert isinstance(ad, ZakupkiRosatomRuAdapter)


def test_build_search_url() -> None:
    url = build_search_url("канцтовары")
    assert "link=procurements" in url
    assert "search=" in url
    assert "zakupki.rosatom.ru" in url


def test_service_rss_rejected() -> None:
    assert is_service_href("https://zakupki.rosatom.ru/rss/ru/tenders")
    assert not is_card_candidate(
        "https://zakupki.rosatom.ru/rss/ru/tenders",
        "rss",
    )


def test_card_by_id_and_href() -> None:
    assert is_card_candidate(None, "Закупка 123456", tender_id="123456")
    assert is_card_candidate(
        "https://zakupki.rosatom.ru/?link=procurement-detail&id=999001",
        "Поставка",
    )


def test_extract_tender_id() -> None:
    assert (
        extract_tender_id("https://zakupki.rosatom.ru/tender/1234567/view")
        == "1234567"
    )
    assert extract_tender_id("https://zakupki.rosatom.ru/?id=555666") == "555666"
    assert extract_tender_id("", "Номер закупки 9876543210") == "9876543210"
