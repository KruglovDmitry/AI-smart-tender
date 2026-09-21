"""Tool: save_tender_overview — один LLM-вызов → один overview.json в папке тендера."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import StructuredTool

from ... import config
from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, ensure_tender_workspace, to_json, trace

# Базовый список полей (ссылка обязательна).
OVERVIEW_FIELDS: list[str] = [
    "tender_url",
    "tender_id",
    "object",
    "customer",
    "price",
    "currency",
    "method",
    "stage",
    "law",
    "placed_at",
    "updated_at",
    "deadline",
    "region",
    "notes",
]

_PROMPT = """Ты извлекаешь структурированные данные с титульной страницы закупки.

Известно заранее:
- tender_id: {tender_id}
- platform: {platform}
- page_url: {page_url}
- page_title: {page_title}
- tender_url (каноническая ссылка на карточку, если известна): {tender_url}

Текст страницы:
\"\"\"
{page_text}
\"\"\"

Верни ТОЛЬКО JSON-объект без markdown со ВСЕМИ ключами из списка (если значения нет — null):
{fields_json}

Правила:
- tender_url обязателен: каноническая ссылка на карточку (tender_url выше или page_url), не выдумывай.
- tender_id — используй известный tender_id, если он есть.
- price — НМЦК / начальная цена одной строкой как на сайте.
- Не добавляй лишних ключей. Не пиши пояснений вне JSON.
"""


def _chat_url() -> str:
    base = config.AGENT_LLM_BASE_URL
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


async def _llm_overview(
    *,
    tender_id: str,
    tender_url: str,
    page_url: str,
    page_title: str,
    page_text: str,
    platform: str,
) -> dict[str, Any]:
    if not config.AGENT_LLM_BASE_URL or not config.AGENT_LLM_API_KEY:
        raise RuntimeError("AGENT_LLM_* not configured")

    prompt = _PROMPT.format(
        tender_id=tender_id,
        platform=platform,
        page_url=page_url,
        page_title=page_title or "",
        tender_url=tender_url or page_url,
        page_text=(page_text or "")[:12000],
        fields_json=json.dumps(OVERVIEW_FIELDS, ensure_ascii=False),
    )
    payload = {
        "model": config.AGENT_LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Ты извлекаешь поля карточки закупки. Отвечай только валидным JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 1500,
    }
    headers = {
        "Authorization": f"Bearer {config.AGENT_LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(_chat_url(), headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:400]}")
        data = resp.json()

    content = (
        ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    )
    raw = _extract_json(content)
    if not raw:
        raise RuntimeError("LLM returned no JSON object")

    out: dict[str, Any] = {}
    for key in OVERVIEW_FIELDS:
        val = raw.get(key, None)
        if val is None or val == "" or val == "null":
            out[key] = None
        elif isinstance(val, (int, float, bool)):
            out[key] = val
        else:
            out[key] = str(val).strip()[:800]

    out["tender_id"] = out.get("tender_id") or tender_id
    out["tender_url"] = out.get("tender_url") or tender_url or page_url
    if not out.get("tender_url"):
        raise RuntimeError("LLM omitted required tender_url")
    out["platform"] = platform
    out["page_url"] = page_url
    out["page_title"] = page_title
    out["extracted_at"] = datetime.now(timezone.utc).isoformat()
    out["page_text_excerpt"] = (page_text or "")[:6000]
    return out


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def save_tender_overview() -> str:
        tender_id = ctx.current_tender_id
        if not tender_id:
            result = {
                "ok": False,
                "action": "save_tender_overview",
                "message": "Сначала extract_tender_id (нет текущего tender_id)",
            }
            trace(ctx, "save_tender_overview", {}, result)
            return to_json(result)

        page = await browser_tools.get_page_text(ctx.rt, max_chars=14000)
        if not page.get("ok"):
            result = {
                "ok": False,
                "action": "save_tender_overview",
                "message": page.get("message") or "get_page_text failed",
            }
            trace(ctx, "save_tender_overview", {}, result)
            return to_json(result)

        page_url = str(page.get("url") or ctx.rt.page.url)
        tender_url = ctx.current_tender_url or page_url
        folder = ensure_tender_workspace(ctx, tender_id, tender_url)

        try:
            payload = await _llm_overview(
                tender_id=tender_id,
                tender_url=tender_url,
                page_url=page_url,
                page_title=str(page.get("title") or ""),
                page_text=str(page.get("text") or ""),
                platform=ctx.platform,
            )
        except Exception as e:
            result = {
                "ok": False,
                "action": "save_tender_overview",
                "message": f"LLM overview failed: {e}",
                "tender_id": tender_id,
                "tender_dir": str(folder),
            }
            trace(ctx, "save_tender_overview", {}, result)
            return to_json(result)

        out_path = Path(folder) / "overview.json"
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result = {
            "ok": True,
            "action": "save_tender_overview",
            "message": f"Saved {out_path.name} into {folder}",
            "tender_id": tender_id,
            "tender_dir": str(folder),
            "overview_path": str(out_path),
            "overview": {
                "tender_url": payload.get("tender_url"),
                "object": payload.get("object"),
                "customer": payload.get("customer"),
                "price": payload.get("price"),
                "deadline": payload.get("deadline"),
                "method": payload.get("method"),
                "stage": payload.get("stage"),
            },
        }
        trace(ctx, "save_tender_overview", {}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=save_tender_overview,
        name="save_tender_overview",
        description=(
            "Один LLM-вызов: сохранить титульный overview.json в папку текущего тендера.\n"
            "Поля: tender_url (обязательная ссылка), tender_id, object, customer, price, "
            "currency, method, stage, law, placed_at, updated_at, deadline, region, notes.\n"
            "КОГДА: на common-info / основной странице закупки, ДО download_url. "
            "Нужен extract_tender_id. Без regex-fallback — при ошибке LLM вернёт ok=false.\n"
            "ВЕРНЁТ JSON: ok, tender_dir, overview_path, overview{...}."
        ),
    )
