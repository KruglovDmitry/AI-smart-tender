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

from . import get_index, pdf

VL_TIMEOUT_SEC = float(os.getenv("TOOLS_VL_TIMEOUT_SEC", "120"))
VL_DPI = int(os.getenv("TOOLS_VL_DPI", "150"))

PROMPT = """You are looking at page {page} of the document "{name}".
Text layer of the page (may be incomplete or garbled; trust the image):
---
{text}
---
Question: {question}
Answer in Russian, precisely and only from what is visible. If something is unreadable or absent, say so."""


def _vl_config() -> tuple[str, str, str]:
    base = os.getenv("TOOLS_VL_BASE_URL") or os.getenv("AV_PLANNER_BASE_URL") or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    key = os.getenv("TOOLS_VL_API_KEY") or os.getenv("AV_PLANNER_API_KEY") or os.getenv("QWEN_API_KEY") or ""
    model = os.getenv("TOOLS_VL_MODEL") or os.getenv("AV_PLANNER_MODEL") or "qwen3-vl-plus"
    if not key:
        raise RuntimeError("Vision model is not configured: set AV_PLANNER_API_KEY (Qwen-VL) or TOOLS_VL_* in .env")
    return base.rstrip("/"), key, model


def view_page(path: str, page: int, question: str) -> dict[str, Any]:
    idx = get_index(path)
    if not idx.is_pdf:
        raise ValueError("view_page works for PDF files only")
    idx.page(page)
    q = (question or "").strip() or "Опиши, что изображено на странице, со всеми подписями и значениями."
    f = idx.cache / "view" / f"{page}_{hashlib.sha1(q.encode('utf-8')).hexdigest()[:12]}.txt"
    if f.exists():
        return {"path": idx.rel, "page": page, "question": q, "answer": f.read_text(encoding="utf-8"), "cached": True}
    text, _ = idx.text(page, run_ocr=False)
    png = pdf.render_png(idx.path, page, dpi=VL_DPI)
    base, key, model = _vl_config()
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                    {"type": "text", "text": PROMPT.format(page=page, name=idx.path.name, text=text[:4000], question=q)},
                ],
            }
        ],
        "temperature": 0,
    }
    r = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
        timeout=httpx.Timeout(VL_TIMEOUT_SEC, connect=10),
    )
    r.raise_for_status()
    answer = r.json()["choices"][0]["message"]["content"].strip()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(answer, encoding="utf-8")
    return {"path": idx.rel, "page": page, "question": q, "answer": answer, "model": model, "cached": False}
