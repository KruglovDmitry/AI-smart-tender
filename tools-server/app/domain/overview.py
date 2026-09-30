"""LLM overview extraction for tender cards."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .. import config
from ..core.llm.client import chat_text, extract_json_object
from ..core.llm.usage import usage_role

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


async def extract_overview(
    *,
    tender_id: str,
    tender_url: str,
    page_url: str,
    page_title: str,
    page_text: str,
    platform: str,
) -> dict[str, Any]:
    """One LLM call → normalized overview dict."""
    prompt = _PROMPT.format(
        tender_id=tender_id,
        platform=platform,
        page_url=page_url,
        page_title=page_title or "",
        tender_url=tender_url or page_url,
        page_text=(page_text or "")[:12000],
        fields_json=json.dumps(OVERVIEW_FIELDS, ensure_ascii=False),
    )
    with usage_role("overview"):
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
    out["source"] = "text"
    return out


_VISION_OVERVIEW_PROMPT = """Ты извлекаешь структурированные данные карточки закупки со скриншотов страницы.

Известно заранее:
- tender_id: {tender_id}
- platform: {platform}
- page_url: {page_url}
- page_title: {page_title}
- tender_url: {tender_url}

Верни ТОЛЬКО JSON-объект без markdown со ВСЕМИ ключами:
{fields_json}

Правила:
- tender_url обязателен.
- tender_id — используй известный, если есть.
- price — как на экране.
- Не добавляй лишних ключей.
"""


async def extract_overview_from_screenshots(
    *,
    rt: Any,
    tender_id: str,
    tender_url: str,
    platform: str,
    max_shots: int = 3,
) -> dict[str, Any]:
    """Up to 3 viewport screenshots (scroll down) → perception model → overview."""
    from ..core.browser.primitives import screenshot
    from ..core.llm.client import chat_completions, message_text
    from ..core.vision import scroll_screen

    shots: list[str] = []
    page_url = ""
    page_title = ""
    try:
        page_url = rt.page.url or ""
        page_title = await rt.page.title()
    except Exception:
        pass

    for i in range(max(1, max_shots)):
        shot = await screenshot(rt)
        if shot.get("ok") and getattr(rt, "last_screenshot_b64", None):
            shots.append(rt.last_screenshot_b64)
        if i + 1 < max_shots:
            await scroll_screen(rt, direction="down", amount="screen")

    if not shots:
        raise RuntimeError("no screenshots for vision overview")

    base = (getattr(config, "AGENT_PERCEPTION_BASE_URL", None) or "").rstrip("/")
    key = getattr(config, "AGENT_PERCEPTION_API_KEY", None) or ""
    model = getattr(config, "AGENT_PERCEPTION_MODEL", None) or "qwen3-vl-plus"
    backend = getattr(config, "AGENT_PERCEPTION_BACKEND", "qwen_vl")
    if backend == "ui_tars":
        from ..core.vision.ui_tars.client import UiTarsClient

        client = UiTarsClient()
        if not client.configured:
            raise RuntimeError("perception ui_tars not configured")
        prompt = _VISION_OVERVIEW_PROMPT.format(
            tender_id=tender_id,
            platform=platform,
            page_url=page_url,
            page_title=page_title or "",
            tender_url=tender_url or page_url,
            fields_json=json.dumps(OVERVIEW_FIELDS, ensure_ascii=False),
        )
        content_parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for b64 in shots:
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )
        with usage_role("perception"):
            api = await client.chat(
                [{"role": "user", "content": content_parts}],
                max_tokens=1500,
            )
        if not api.get("ok"):
            raise RuntimeError(api.get("error") or "ui_tars overview failed")
        content = str(api.get("content") or "")
    else:
        if not base or not key:
            raise RuntimeError(
                "perception не настроен (AGENT_PERCEPTION_BASE_URL / API_KEY)"
            )
        prompt = _VISION_OVERVIEW_PROMPT.format(
            tender_id=tender_id,
            platform=platform,
            page_url=page_url,
            page_title=page_title or "",
            tender_url=tender_url or page_url,
            fields_json=json.dumps(OVERVIEW_FIELDS, ensure_ascii=False),
        )
        content_parts = [{"type": "text", "text": prompt}]
        for b64 in shots:
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )
        with usage_role("perception"):
            data = await chat_completions(
                [{"role": "user", "content": content_parts}],
                model=model,
                tools=None,
                tool_choice=None,
                temperature=0,
                max_tokens=1500,
                timeout=120.0,
                base_url=base,
                api_key=key,
            )
        content = message_text((data.get("choices") or [{}])[0].get("message"))

    raw = extract_json_object(content)
    if not raw:
        raise RuntimeError("perception returned no JSON object for overview")

    out: dict[str, Any] = {}
    for key_name in OVERVIEW_FIELDS:
        val = raw.get(key_name, None)
        if val is None or val == "" or val == "null":
            out[key_name] = None
        elif isinstance(val, (int, float, bool)):
            out[key_name] = val
        else:
            out[key_name] = str(val).strip()[:800]

    out["tender_id"] = out.get("tender_id") or tender_id
    out["tender_url"] = out.get("tender_url") or tender_url or page_url
    if not out.get("tender_url"):
        raise RuntimeError("overview omitted required tender_url")
    out["platform"] = platform
    out["page_url"] = page_url
    out["page_title"] = page_title
    out["extracted_at"] = datetime.now(timezone.utc).isoformat()
    out["page_text_excerpt"] = None
    out["source"] = "vision"
    out["screenshots"] = len(shots)
    return out


def looks_stale(payload: dict[str, Any]) -> tuple[bool, str]:
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
