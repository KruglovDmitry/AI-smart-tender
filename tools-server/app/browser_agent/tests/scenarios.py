"""Сценарии 1–4 для интеграционных тестов platform-агента."""

from __future__ import annotations

from typing import Any

SCENARIOS: dict[int, dict[str, Any]] = {
    1: {
        "id": 1,
        "name": "open_platform",
        "max_steps": 10,
        "max_new_tenders": 1,
        "instruction": (
            "СЦЕНАРИЙ 1 (только открытие). Платформа уже открыта или открой platform_url. "
            "Проверь загрузку (get_page_text). НЕ ищи, НЕ кликай поиск. "
            "finish_platform_task(success=true) с URL и title."
        ),
    },
    2: {
        "id": 2,
        "name": "search_keywords",
        "max_steps": 20,
        "max_new_tenders": 1,
        "instruction": (
            "СЦЕНАРИЙ 2 (поиск). Предпочтительно navigate сразу на "
            "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString=<urlencoded keywords>. "
            "Либо screenshot→type_text с обязательными x,y→Enter; иначе eval_js заполнить searchString. "
            "НЕ вызывай finish пока URL не содержит results.html или searchString. "
            "НЕ открывай карточки, НЕ качай. finish: URL выдачи."
        ),
    },
    3: {
        "id": 3,
        "name": "open_first_card",
        "max_steps": 30,
        "max_new_tenders": 1,
        "instruction": (
            "СЦЕНАРИЙ 3 (первая карточка). Поиск по keywords → открой РОВНО ПЕРВУЮ карточку "
            "в порядке выдачи (первый валидный a[href*=regNumber]/notice/ea44|/notice/notice223|/notice/ok44] "
            "сверху списка; НЕ выбирай «удобнее» ниже по списку). "
            "Пропусти только electronic/fcs или уже seen — тогда сразу следующий по порядку. "
            "НЕ качай документы. В finish_platform_task передай РЕАЛЬНЫЙ URL и tender_id из tool results "
            "(не плейсхолдеры вроде result['...'])."
        ),
    },
    4: {
        "id": 4,
        "name": "download_docs",
        "max_steps": 40,
        "max_new_tenders": 1,
        "instruction": (
            "СЦЕНАРИЙ 4 (документы). Поиск → выбери карточку ТОЛЬКО ea44/notice223/ok44 (не electronic/fcs). "
            "Если seen или 404/0 files — следующий кандидат. "
            "Вкладка Документы → скачай 1–3 файла kind=file (не футер). mark_tender_seen. "
            "finish_platform_task: что скачал."
        ),
    },
}
