"""Tool: save_tender_overview — один LLM-вызов → один overview.json в папке тендера."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool

from ... import config
from ...core.browser import primitives as browser_tools
from ...core.llm.client import chat_text, extract_json_object
from ..context import PlatformAgentContext, ensure_tender_workspace, to_json, trace
from ...domain.tender_id import resolve_tender_id

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
- price — начальная/максимальная цена одной строкой как на сайте.
- Не добавляй лишних ключей. Не пиши пояснений вне JSON.
"""


async def _llm_overview(
    *,
    tender_id: str,
    tender_url: str,
    page_url: str,
    page_title: str,
    page_text: str,
    platform: str,
) -> dict[str, Any]:
    prompt = _PROMPT.format(
        tender_id=tender_id,
        platform=platform,
        page_url=page_url,
        page_title=page_title or "",
        tender_url=tender_url or page_url,
        page_text=(page_text or "")[:12000],
        fields_json=json.dumps(OVERVIEW_FIELDS, ensure_ascii=False),
    )
    content = await chat_text(
        [
            {
                "role": "system",
                "content": "Ты извлекаешь поля карточки закупки. Отвечай только валидным JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        model=config.AGENT_PRIMARY_MODEL,
        temperature=0,
        max_tokens=1500,
        timeout=90.0,
    )
    raw = extract_json_object(content)
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


def _looks_stale(payload: dict[str, Any]) -> tuple[bool, str]:
    """Universal date/stage heuristic — not tied to a specific platform."""
    year_now = datetime.now(timezone.utc).year
    blob = " ".join(
        str(payload.get(k) or "")
        for k in ("deadline", "placed_at", "updated_at", "stage", "notes", "object")
    )
    years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", blob)]
    stage = str(payload.get("stage") or "").lower()
    if years and max(years) <= year_now - 3:
        return True, f"dates look old (max_year={max(years)}, now={year_now})"
    if any(
        x in stage
        for x in (
            "заверш",
            "отмен",
            "архив",
            "completed",
            "cancelled",
            "canceled",
            "closed",
        )
    ) and (not years or max(years) < year_now):
        return True, f"stage suggests inactive: {payload.get('stage')!r}"
    return False, ""


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def save_tender_overview() -> str:
        page_url = str(ctx.rt.page.url or "")
        # Всегда берём id с ТЕКУЩЕЙ страницы — иначе после 1-го тендера
        # ctx.current_tender_id залипает и всё пишется в одну папку.
        resolved = resolve_tender_id(page_url, platform=ctx.platform)
        page_tid = str(resolved.get("tender_id") or "").strip() or None
        tender_id = page_tid or (str(ctx.current_tender_id or "").strip() or None)
        if tender_id:
            ctx.current_tender_id = tender_id
            if page_tid:
                ctx.current_tender_url = page_url
            elif not ctx.current_tender_url:
                ctx.current_tender_url = page_url
        if not tender_id:
            result = {
                "ok": False,
                "action": "save_tender_overview",
                "message": (
                    "Нет tender_id: вызови filter_unseen_tenders / extract_tender_id "
                    "или открой карточку закупки с id в URL"
                ),
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
        # Перечитать id после возможного редиректа
        resolved2 = resolve_tender_id(page_url, platform=ctx.platform)
        page_tid2 = str(resolved2.get("tender_id") or "").strip() or None
        if page_tid2:
            tender_id = page_tid2
            ctx.current_tender_id = tender_id
            ctx.current_tender_url = page_url
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
        try:
            from ...domain import manifest as manifest_mod

            manifest_mod.upsert_overview_fields(
                folder,
                {
                    **payload,
                    "platform": ctx.platform,
                    "tender_id": tender_id,
                },
            )
        except Exception:
            pass
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
                "placed_at": payload.get("placed_at"),
            },
        }
        stale, reason = _looks_stale(payload)
        if stale:
            result["looks_stale"] = True
            result["stale_reason"] = reason
            result["hint"] = (
                "Кандидат похож на устаревший/неактивный: не качай документы; "
                "mark_tender_seen(..., count_toward_limit=false) и бери следующий new[]."
            )
            result["message"] += " | looks_stale=true"
        trace(ctx, "save_tender_overview", {}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=save_tender_overview,
        name="save_tender_overview",
        description=(
            "Один LLM-вызов: сохранить титульный overview.json в папку текущего тендера.\n"
            "Поля: tender_url (обязательная ссылка), tender_id, object, customer, price, "
            "currency, method, stage, law, placed_at, updated_at, deadline, region, notes.\n"
            "КОГДА: на титульной/основной странице карточки закупки, ДО download_url. "
            "Нужен известный tender_id (filter/extract или id в URL). "
            "Без regex-fallback — при ошибке LLM вернёт ok=false.\n"
            "ВЕРНЁТ JSON: ok, tender_dir, overview_path, overview{...}."
        ),
    )
