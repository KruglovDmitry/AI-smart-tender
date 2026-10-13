"""Известные параметры площадок: как получить выдачу «по дате публикации, сначала новые».

Это данные о площадке, а не о вёрстке: параметры адреса меняются редко, а
кнопки сортировки часто сделаны не ссылкой и не кнопкой (ЕИС), и DOM-агент их
не видит. Если площадки здесь нет, сортировку включает vision-шаг.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# host → (часть пути выдачи, параметры сортировки)
_SORT_BY_DATE: dict[str, tuple[str, dict[str, str]]] = {
    "zakupki.gov.ru": ("/extendedsearch/results", {"sortBy": "PUBLISH_DATE", "sortDirection": "false"}),
    "zakupki.mos.ru": ("/purchase/list", {"sortField": "PublishDate", "sortDesc": "true"}),
}


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
