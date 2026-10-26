"""Разбор текста задания: площадка, запрос, сколько записей, сортировка, скачивание."""

from __future__ import annotations

import re

from ..api.schemas import TaskSpec
from ..config import Settings

_URL = re.compile(r"https?://[^\s,;]+", re.I)
_QUOTED = re.compile(r"[\"«](.+?)[\"»]")
# Адрес без схемы: модель чата пишет «на zakupki.gov.ru» — так же, как человек.
_DOMAIN = re.compile(r"(?<![\w.@/-])((?:[a-z0-9-]+\.)+(?:ru|su|com|net|org|info|pro|online)(?:/[^\s,;]*)?)(?![\w-])", re.I)
# Слово без кавычек: «по ключевому слову ИБП, …», «3 последние закупки ИБП и скачай …».
_END = r"(?=\s*[,.;(]|\s+(?:и|с|со|включая|за|на|в|по|для|без)\s|\s*$)"
_UNQUOTED = (re.compile(r"(?:по\s+(?:ключевому\s+)?слову|по\s+запросу)\s+(.+?)" + _END, re.I),
             re.compile(r"закуп\w*\s+(?!по\s|на\s|с\s|в\s|и\s)(.+?)" + _END, re.I))
_NUM_WORDS = {
    "один": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
}


class TaskParseError(ValueError):
    pass


def _limit_token(text: str, default: int) -> int:
    match = re.search(r"(?:последн\w+|перв\w+)\s+(\d+|[а-яё]+)", text, re.I)
    if match and not match.group(1).isdigit() and match.group(1).casefold() not in _NUM_WORDS:
        match = None  # «3 последние закупки»: число стоит перед словом
    if not match:
        match = re.search(r"(\d+|[а-яё]+)\s+(?:последн\w+|перв\w+|свеж\w+|нов\w+)\s+закуп", text, re.I)
        if match and not match.group(1).isdigit() and match.group(1).casefold() not in _NUM_WORDS:
            match = None
    if not match:
        match = re.search(r"(\d+)\s+закуп", text, re.I)
    if not match:
        return default
    token = match.group(1).casefold()
    if token.isdigit():
        return max(1, int(token))
    return _NUM_WORDS.get(token, default)


def parse_task(text: str, settings: Settings) -> TaskSpec:
    raw = (text or "").strip()
    if not raw:
        raise TaskParseError("Пустое задание.")
    found = _URL.search(raw)
    if found:
        url = found.group(0).rstrip(".,)")
    elif bare := _DOMAIN.search(raw):
        url = "https://" + bare.group(1).rstrip(".,)")
    else:
        raise TaskParseError("В задании нет URL площадки.")
    quoted = [item.strip() for item in _QUOTED.findall(raw) if item.strip()]
    topic = quoted[0] if quoted else ""
    if not topic:
        themed = re.search(r"на тему\s+(.+?)\s*$", raw, re.I)
        if themed:
            topic = themed.group(1).strip(" «».")
    for pattern in _UNQUOTED:
        if topic:
            break
        unquoted = pattern.search(_URL.sub(" ", raw))
        if unquoted:
            topic = unquoted.group(1).strip(" «».")
    if not topic:
        raise TaskParseError("Не удалось однозначно определить ключевое слово или тему.")
    sort_by = "published_at" if re.search(r"последн|дат[аеуы]\s+публикац", raw, re.I) else None
    filters: dict[str, str] = {}
    if re.search(r"фильтр по дате|дат[аеуы]\s+публикац", raw, re.I):
        filters["published_at"] = "latest"
        sort_by = "published_at"
    return TaskSpec(
        url=url,
        topic=topic,
        keywords=[topic],
        limit=_limit_token(raw, settings.default_limit),
        sort_by=sort_by,  # type: ignore[arg-type]
        filters=filters,
        download_documents=bool(re.search(r"скач", raw, re.I))
        and not re.search(r"(?:\bне|\bбез|ничего\s+не)\s+(?:\w+\s+){0,2}скач", raw, re.I),
        active_only=not re.search(r"архив|заверш|прошедш|закрыт|все\s+закупки|за\s+всё\s+время", raw, re.I),
        max_pages=settings.max_pages,
        max_actions=settings.max_actions,
        timeout_seconds=settings.timeout_seconds,
    )
