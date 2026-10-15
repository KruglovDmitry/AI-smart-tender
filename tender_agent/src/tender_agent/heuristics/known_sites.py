"""Известные параметры площадок: как получить выдачу «по дате публикации, сначала новые».

Это данные о площадке, а не о вёрстке: параметры адреса меняются редко, а
кнопки сортировки часто сделаны не ссылкой и не кнопкой (ЕИС), и DOM-агент их
не видит. Если площадки здесь нет, сортировку включает vision-шаг.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

# host → (часть пути выдачи, параметры сортировки)
_SORT_BY_DATE: dict[str, tuple[str, dict[str, str]]] = {
    "zakupki.gov.ru": ("/extendedsearch/results", {"sortBy": "PUBLISH_DATE", "sortDirection": "false"}),
    "zakupki.mos.ru": ("/purchase/list", {"sortField": "PublishDate", "sortDesc": "true"}),
    "tektorg.ru": ("/procedures", {"sort": "datePublished_desc"}),
}

# host → шаблон выдачи по запросу (подставляется {query}); выручает, когда поле поиска
# не отправляется из DOM (ТЭК-Торг) — дешевле, чем vision-шаг.
_SEARCH_URL: dict[str, str] = {
    "zakupki.gov.ru": "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString={query}",
    "zakupki.mos.ru": (
        "https://zakupki.mos.ru/purchase/list?page=1&perPage=10&sortField=PublishDate&sortDesc=true"
        "&filter=%7B%22nameLike%22%3A%7B%22value%22%3A%22{query}%22%2C%22contains%22%3Atrue%7D%7D"
        "&state=%7B%22currentTab%22%3A2%7D"
    ),
    "tektorg.ru": "https://www.tektorg.ru/procedures?name={query}",
    "fabrikant.ru": "https://www.fabrikant.ru/procedure/search/purchases?page_number=1&query={query}",
    "b2b-center.ru": "https://www.b2b-center.ru/app/next/market-search?q={query}&trade=buy&show=actual&sort=date_desc",
}


def search_url(url: str, keyword: str) -> str | None:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    template = _SEARCH_URL.get(host)
    return template.format(query=quote(keyword)) if template else None


def sorted_by_date_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    known = _SORT_BY_DATE.get(host)
    if not known or known[0] not in parsed.path:
        return None
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if all(query.get(key) == value for key, value in known[1].items()):
        return None
    query.update(known[1])
    return urlunparse(parsed._replace(query=urlencode(query)))
