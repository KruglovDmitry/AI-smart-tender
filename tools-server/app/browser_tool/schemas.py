"""OpenAI-compatible tool schemas for the browser agent."""

from __future__ import annotations

BROWSER_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "Open a URL in the browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http(s) URL to open"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": (
                "Capture the current viewport screenshot. "
                "Use when you need to SEE the page (buttons, layout) or verify state. "
                "After this, you get an image and may click by coordinates."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_xy",
            "description": (
                "Click at viewport coordinates (pixels). "
                "Usually after screenshot. Set expect_download=true if the click should start a file download."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "expect_download": {
                        "type": "boolean",
                        "description": "True if click should trigger a file download",
                        "default": False,
                    },
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into the focused field. Optionally click (x,y) first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "clear": {"type": "boolean", "default": True},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Press a keyboard key (Enter, Tab, Escape, ...).",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string", "default": "Enter"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll the page vertically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delta_y": {"type": "integer", "default": 600},
                    "times": {"type": "integer", "default": 1},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait for N seconds (page loading, animations).",
            "parameters": {
                "type": "object",
                "properties": {"seconds": {"type": "number", "default": 1}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_page_text",
            "description": (
                "Read visible text from the page DOM (no vision). "
                "Good for tender titles, numbers, descriptions."
            ),
            "parameters": {
                "type": "object",
                "properties": {"max_chars": {"type": "integer", "default": 12000}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_download_links",
            "description": (
                "Scan the DOM for likely document/download links and buttons "
                "(pdf, docx, zip, 'скачать', etc.). Prefer this before screenshot "
                "when looking for attachments."
            ),
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 40}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_url",
            "description": (
                "Download a direct file URL (from list_download_links) into the server downloads folder. "
                "Uses the browser session cookies. "
                "Pass suggested_name from the link text when available (correct Cyrillic names)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "suggested_name": {
                        "type": "string",
                        "description": "Optional filename from page link text",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": (
                "Call when the task is complete or cannot proceed. "
                "Provide a short summary and success flag."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "success": {"type": "boolean", "default": True},
                },
                "required": ["summary"],
            },
        },
    },
]

SYSTEM_PROMPT = """Ты — browser-агент для тендерных страниц.

Тебе дают задание (открыть URL, поиск, карточка тендера, скачать документы).
У тебя есть инструменты. Ты сам выбираешь порядок и стратегию:
- DOM-путь: list_download_links, get_page_text, download_url — быстро и дёшево
- Vision-путь: screenshot — после него VL-модель опишет экран текстом
  (координаты/кнопки). Используй этот текст для click_xy / type_text / scroll.
- Можно комбинировать: сначала DOM, если пусто — screenshot

Правила:
1. Сначала navigate на целевой URL (если ещё не открыт).
2. Ввод в поля (поиск, фильтры): НЕ вызывай type_text без координат.
   Сделай screenshot → возьми x,y поля из VL → type_text(text, x, y).
   Для поиска на ЕИС после ввода предпочитай press_key Enter (надёжнее клика по иконке).
   Не кликай «лупу» до ввода текста.
3. После поиска/навигации ПРОВЕРЬ факт успеха по tool-результатам:
   - URL изменился (results.html / searchString=…), ИЛИ
   - get_page_text показывает выдачу/карточку, отличную от предыдущей.
   Если URL всё ещё home.html — поиск НЕ сработал: повтори (новый screenshot + Enter), не выдумывай успех.
4. finish — ТОЛЬКО через tool call `finish` (никогда не пиши finish текстом в ответе).
   success=true только если критерии задания подтверждены URL/текстом/файлами из tool results.
   success=false если не удалось, с краткой причиной.
5. Для документов сначала list_download_links; прямые http(s) ссылки — download_url
   (передавай suggested_name из текста ссылки).
6. На ЕИС документы часто на вкладке «Документы» / «Документы закупки» —
   кликни по ней (click_xy после screenshot/VL).
7. Если одно и то же имя в нескольких редакциях — достаточно ПОСЛЕДНЕЙ + уникальных
   «Решение…» / протоколов; не качай все копии без нужды. Если «скачай всё» — качай всё.
8. Если ссылок нет или только кнопки без href — screenshot → VL → клики.
9. Не выдумывай координаты без VL-анализа свежего screenshot.
10. Не зацикливайся на scroll: после 2–3 прокруток без прогресса — смена стратегии или finish.
11. Если логин/капча/ЭЦП — finish(success=false) с объяснением.
12. Каждый ход — tool call(ы). Финальный итог только через tool finish.
"""
