"""Веб: поиск в интернете и чтение найденной страницы."""

from .fetch import fetch_page
from .search import check_search, parse_duckduckgo, search_web

__all__ = ["check_search", "fetch_page", "parse_duckduckgo", "search_web"]
