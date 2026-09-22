"""Unit tests for bump_page_url."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.core.browser.url_utils import bump_page_url


def test_page_number_plus_one() -> None:
    url = "https://example.com/results.html?searchString=x&pageNumber=1"
    out = bump_page_url(url)
    assert out is not None
    qs = parse_qs(urlparse(out).query)
    assert qs["pageNumber"] == ["2"]


def test_offset_plus_ten() -> None:
    url = "https://example.com/search?q=a&offset=0"
    out = bump_page_url(url)
    assert out is not None
    qs = parse_qs(urlparse(out).query)
    assert qs["offset"] == ["10"]


def test_search_without_page_adds_page_number_2() -> None:
    url = "https://example.com/extendedsearch/results.html?searchString=канц"
    out = bump_page_url(url)
    assert out is not None
    qs = parse_qs(urlparse(out).query)
    assert qs["pageNumber"] == ["2"]


def test_non_search_without_page_returns_none() -> None:
    assert bump_page_url("https://example.com/about") is None
