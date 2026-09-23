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
            "Проверь загрузку (dom_snapshot или inspect_screen). НЕ ищи. "
            "finish(success=true) с URL и title."
        ),
    },
    2: {
        "id": 2,
        "name": "search_keywords",
        "max_steps": 20,
        "max_new_tenders": 1,
        "instruction": (
            "СЦЕНАРИЙ 2 (поиск). open_platform_search(keywords) или navigate на results URL. "
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
            "СЦЕНАРИЙ 3 (первая карточка). open_platform_search → list_new_cards → "
            "open_tender(первый new[].tender_url). "
            "Успех ТОЛЬКО если URL — карточка (не results/search). "
            "НЕ качай документы. finish: реальные URL и tender_id из tool results."
        ),
    },
    4: {
        "id": 4,
        "name": "download_docs",
        "max_steps": 40,
        "max_new_tenders": 1,
        "instruction": (
            "СЦЕНАРИЙ 4 (документы). open_platform_search → list_new_cards → open_tender → "
            "save_overview → list_tender_documents → download_document (1–3 файла) → "
            "mark_processed. finish: что скачал и путь папки."
        ),
    },
}
