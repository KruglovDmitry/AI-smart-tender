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
            "Либо screenshot→type_text с обязательными x,y→Enter. "
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
            "СЦЕНАРИЙ 3 (первая карточка). Поиск по keywords → collect_card_urls → "
            "filter_unseen_tenders → navigate(первый new[].tender_url). "
            "НЕ click_xy вместо navigate, если href уже есть. "
            "Успех ТОЛЬКО если текущий URL после navigate — карточка (не results/search). "
            "Если после клика/перехода всё ещё выдача — НЕ finish(success=true), повтори navigate. "
            "extract_tender_id + check_tender_seen; seen → следующий href по порядку. "
            "НЕ качай документы. finish: реальные URL и tender_id из tool results."
        ),
    },
    4: {
        "id": 4,
        "name": "download_docs",
        "max_steps": 40,
        "max_new_tenders": 1,
        "instruction": (
            "СЦЕНАРИЙ 4 (документы). Поиск → карточка → extract_tender_id → "
            "save_tender_overview на common-info → вкладка Документы → "
            "скачай 1–3 файла kind=file в папку тендера (не футер). mark_tender_seen. "
            "finish_platform_task: что скачал и путь папки."
        ),
    },
}
