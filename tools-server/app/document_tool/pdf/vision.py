"""«Посмотреть страницу»: страница рисуется картинкой и VL-модель отвечает на конкретный вопрос по ней
(схема подключения, чертёж, фото, таблица картинкой). Ответ кэшируется по странице и вопросу.

Модель: TOOLS_VL_* или, если не заданы, та же, что у запасного зрения агента (AV_PLANNER_*, Qwen-VL).
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

import httpx

from . import read as pdf

VL_TIMEOUT_SEC = float(os.getenv("TOOLS_VL_TIMEOUT_SEC", "120"))
VL_DPI = int(os.getenv("TOOLS_VL_DPI", "150"))

PROMPT = """You are looking at page {page} of the document "{name}".
Text layer of the page (may be incomplete or garbled; trust the image):
---
{text}
---
Question: {question}
Answer in Russian, precisely and only from what is visible. If something is unreadable or absent, say so."""


from ..vision import _vl_config, inspect_images


def view_page(path: str, page: int, question: str, bbox: list[float] | None = None, detail: str = "auto") -> dict[str, Any]:
    from ...document_index import get_index

    idx = get_index(path)
    if not idx.is_pdf:
        raise ValueError("view_page works for PDF files only")
    idx.page(page)
    q = (question or "").strip() or "Опиши, что изображено на странице, со всеми подписями и значениями."
    if detail not in {"auto", "low", "high"}:
        raise ValueError("detail must be auto, low or high")
    dpi = {"auto": VL_DPI, "low": 100, "high": 250}[detail]
    base, key, model = _vl_config()
    text, _ = idx.text(page, run_ocr=False)
    if bbox is not None:
        q = "Изображение — выделенная область страницы; отвечай только по ней. " + q
    prompt = PROMPT.format(page=page, name=idx.path.name, text=text[:4000], question=q)
    result = inspect_images(idx, page, ['pdf-page', page, bbox, detail, dpi],
        lambda: [pdf.render_png(idx.path, page, dpi=dpi, bbox=bbox)], prompt, config=(base, key, model))
    return {"path":idx.rel,"page":page,"bbox":bbox,"detail":detail,"question":q,**result}
