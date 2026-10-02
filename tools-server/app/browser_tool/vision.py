"""VL helper: describe screenshot for the tool-calling agent (no function calling)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from .. import config

logger = logging.getLogger(__name__)

VL_PROMPT = """Ты смотришь на screenshot страницы тендера / веб-страницы.
Viewport: {width}x{height} px. URL: {url}

Задача агента: {task}

Опиши UI и предложи следующие действия browser-агенту.
Верни ТОЛЬКО JSON без markdown:
{{
  "page_summary": "кратко что на экране",
  "has_download_ui": true/false,
  "download_hints": ["текст кнопок/ссылок связанных со скачиванием"],
  "suggested_actions": [
    {{
      "action": "click_xy|scroll|type_text|none",
      "x": 0,
      "y": 0,
      "expect_download": false,
      "text": "",
      "delta_y": 600,
      "reason": "зачем"
    }}
  ],
  "blockers": ["login","captcha","empty","none"],
  "confidence": 0.0
}}

Координаты x,y — в пикселях viewport (0,0 = левый верхний угол).
Если документов/кнопок скачивания не видно — так и напиши, предложи scroll.
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


async def analyze_screenshot(
    *,
    image_b64: str,
    width: int,
    height: int,
    url: str,
    task: str,
) -> dict[str, Any]:
    """
    Call VL model with the screenshot (same style as AI-tender catalog VL).
    Returns structured advice for the tool-calling agent.
    """
    if not config.AGENT_LLM_BASE_URL or not config.AGENT_LLM_API_KEY:
        return {
            "ok": False,
            "error": "LLM not configured",
            "raw": "",
            "advice": {},
        }

    model = config.AGENT_VL_MODEL
    prompt = VL_PROMPT.format(
        width=width,
        height=height,
        url=url or "",
        task=(task or "")[:1500],
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}"
                        },
                    },
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {config.AGENT_LLM_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(_chat_url(), headers=headers, json=payload)
            if resp.status_code >= 400:
                logger.warning("VL error %s: %s", resp.status_code, resp.text[:400])
                return {
                    "ok": False,
                    "error": f"VL HTTP {resp.status_code}: {resp.text[:400]}",
                    "model": model,
                    "raw": "",
                    "advice": {},
                }
            data = resp.json()
            content = (
                ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
                or ""
            )
            advice = _extract_json(content)
            return {
                "ok": True,
                "model": model,
                "raw": content[:4000],
                "advice": advice,
            }
    except Exception as e:
        logger.exception("VL analyze failed")
        return {
            "ok": False,
            "error": str(e),
            "model": model,
            "raw": "",
            "advice": {},
        }


def format_vl_for_agent(vl_result: dict[str, Any]) -> str:
    """Compact text observation for the tool-calling model (no image)."""
    if not vl_result.get("ok"):
        return (
            "VL analysis failed: "
            f"{vl_result.get('error') or 'unknown'}. "
            "Continue with DOM tools (list_download_links / get_page_text) "
            "or retry screenshot later."
        )
    advice = vl_result.get("advice") or {}
    payload = {
        "vl_model": vl_result.get("model"),
        "page_summary": advice.get("page_summary"),
        "has_download_ui": advice.get("has_download_ui"),
        "download_hints": advice.get("download_hints"),
        "suggested_actions": advice.get("suggested_actions"),
        "blockers": advice.get("blockers"),
        "confidence": advice.get("confidence"),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "VL screenshot analysis (use this instead of raw pixels). "
        "You may follow suggested click_xy / scroll, or ignore and use DOM tools.\n"
        + text
    )
