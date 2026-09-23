"""Unified OpenAI-compatible chat client (tool-calling + plain + multimodal)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ... import config

logger = logging.getLogger(__name__)


def chat_url() -> str:
    base = (config.AGENT_LLM_BASE_URL or "").rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def require_llm() -> None:
    if not config.AGENT_LLM_BASE_URL:
        raise RuntimeError(
            "AGENT_LLM_BASE_URL is not set. "
            "Example: https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )
    if not config.AGENT_LLM_API_KEY:
        raise RuntimeError("AGENT_LLM_API_KEY is not set")


def extract_json_object(text: str) -> dict[str, Any]:
    """Best-effort parse of a JSON object from model text."""
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


def message_text(message: dict[str, Any] | None) -> str:
    if not message:
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        return "\n".join(t for t in parts if t)
    return ""


async def chat_completions(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = "auto",
    temperature: float = 0.1,
    max_tokens: int | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """
    POST /chat/completions. Returns raw API JSON.
    model defaults to AGENT_PRIMARY_MODEL (falls back to AGENT_LLM_MODEL).
    """
    require_llm()
    use_model = model or getattr(config, "AGENT_PRIMARY_MODEL", None) or config.AGENT_LLM_MODEL
    payload: dict[str, Any] = {
        "model": use_model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    headers = {
        "Authorization": f"Bearer {config.AGENT_LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(chat_url(), headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM error {resp.status_code}: {resp.text[:800]}")
        return resp.json()


async def chat_text(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    timeout: float = 90.0,
) -> str:
    """Convenience: return assistant message text content."""
    data = await chat_completions(
        messages,
        model=model,
        tools=None,
        tool_choice=None,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    choice = (data.get("choices") or [{}])[0]
    return message_text(choice.get("message") or {})
