"""Unified OpenAI-compatible chat client (tool-calling + plain + multimodal)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ... import config
from .usage import record_usage, usage_from_response

logger = logging.getLogger(__name__)


def deepseek_endpoint(model: str | None = None) -> bool:
    """True when the call goes to DeepSeek (thinking defaults to on)."""
    name = (model or "").lower()
    base = (config.AGENT_LLM_BASE_URL or "").lower()
    return "deepseek" in name or "deepseek.com" in base


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
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    POST /chat/completions. Returns raw API JSON.
    model defaults to AGENT_PRIMARY_MODEL (falls back to AGENT_LLM_MODEL).
    Optional base_url/api_key override primary AGENT_LLM_* (perception).
    """
    override = bool((base_url or "").strip())
    if not override:
        require_llm()
        url = chat_url()
        key = config.AGENT_LLM_API_KEY
    else:
        base = str(base_url).rstrip("/")
        url = (
            base
            if base.endswith("/chat/completions")
            else f"{base}/chat/completions"
        )
        key = (api_key or "").strip()
        if not key:
            raise RuntimeError("api_key required when base_url override is set")

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
    # deepseek-flash thinks by default; agent + overview need the final answer only.
    if deepseek_endpoint(use_model) and not override:
        payload["thinking"] = {"type": "disabled"}

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM error {resp.status_code}: {resp.text[:800]}")
        data = resp.json()
        record_usage(usage_from_response(data))
        return data


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
