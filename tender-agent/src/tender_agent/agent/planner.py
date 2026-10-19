"""Разбор текста задания: площадка, запрос, сколько записей, сортировка, скачивание."""

from __future__ import annotations

import re

from ..api.schemas import TaskSpec
from ..config import Settings

_URL = re.compile(r"https?://[^\s,;]+", re.I)
_QUOTED = re.compile(r"[\"«](.+?)[\"»]")
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
    if not found:
        raise TaskParseError("В задании нет URL площадки.")
    url = found.group(0).rstrip(".,)")
    quoted = [item.strip() for item in _QUOTED.findall(raw) if item.strip()]
    topic = quoted[0] if quoted else ""
    if not topic:
        themed = re.search(r"на тему\s+(.+?)\s*$", raw, re.I)
        if themed:
            topic = themed.group(1).strip(" «».")
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
        max_pages=settings.max_pages,
        max_actions=settings.max_actions,
        timeout_seconds=settings.timeout_seconds,
    )
