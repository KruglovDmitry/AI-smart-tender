"""Unit tests for classify_page_kind (no Playwright)."""

from __future__ import annotations

from app.core.browser.page_kind import classify_page_kind


def test_not_found() -> None:
    info = classify_page_kind(
        "https://example.com/x",
        title="Страница не найдена",
        body="Ошибка 404",
    )
    assert info["page_kind"] == "not_found"


def test_login() -> None:
    info = classify_page_kind(
        "https://example.com/login",
        title="Вход",
        body="Авторизация. Введите пароль.",
    )
    assert info["page_kind"] == "login"


def test_search() -> None:
    info = classify_page_kind(
        "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString=x",
        title="Результаты поиска",
        body="",
    )
    assert info["page_kind"] == "search"


def test_documents() -> None:
    info = classify_page_kind(
        "https://zakupki.gov.ru/epz/order/notice/notice223/documents.html?purchaseNoticeNumber=1",
        title="Документы",
        body="",
    )
    assert info["page_kind"] == "documents"


def test_card() -> None:
    info = classify_page_kind(
        "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=1",
        title="Извещение",
        body="",
    )
    assert info["page_kind"] == "card"


def test_home() -> None:
    info = classify_page_kind("https://zakupki.gov.ru/", title="ЕИС", body="")
    assert info["page_kind"] == "home"
