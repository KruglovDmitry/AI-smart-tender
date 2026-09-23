"""OpenAI-compatible async vLLM client for UI-TARS-1.5."""

from __future__ import annotations

import time
from typing import Any

import httpx

from .... import config
from .prompts import GROUNDING_PROMPT


class UiTarsClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 180.0,
        api_key: str | None = None,
    ) -> None:
        raw = (
            base_url
            if base_url is not None
            else (getattr(config, "UI_TARS_BASE_URL", None) or "")
        )
        self.base_url = str(raw).rstrip("/")
        self.model = model or getattr(config, "UI_TARS_MODEL", None) or "ui-tars"
        self.timeout = timeout
        self.api_key = api_key or getattr(config, "UI_TARS_API_KEY", "") or "EMPTY"

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 256,
        temperature: float = 0,
    ) -> dict[str, Any]:
        """POST /v1/chat/completions (non-blocking)."""
        if not self.base_url:
            return {
                "ok": False,
                "content": "",
                "latency_ms": 0,
                "error": "UI_TARS_BASE_URL is not set",
            }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
        except Exception as e:
            return {
                "ok": False,
                "content": "",
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "error": str(e),
            }
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code >= 400:
            return {
                "ok": False,
                "content": "",
                "latency_ms": latency_ms,
                "error": f"HTTP {r.status_code}: {r.text[:400]}",
            }
        data = r.json()
        content = (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        )
        return {
            "ok": True,
            "content": content,
            "latency_ms": latency_ms,
            "raw": data,
        }

    async def ground(self, image_b64: str, instruction: str) -> dict[str, Any]:
        """Mode A: GROUNDING prompt → raw model text + latency."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": GROUNDING_PROMPT.format(
                            instruction=(instruction or "")[:800]
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}"
                        },
                    },
                ],
            }
        ]
        return await self.chat(messages, max_tokens=256)

    async def probe(self, timeout: float = 3.0) -> dict[str, Any]:
        """Lightweight readiness check for /health (GET /v1/models)."""
        if not self.base_url:
            return {
                "ok": False,
                "configured": False,
                "note": "UI_TARS_BASE_URL is empty — set it in .env",
            }
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(
                    f"{self.base_url}/v1/models",
                    headers=self._headers(),
                )
            latency_ms = int((time.perf_counter() - t0) * 1000)
            if r.status_code >= 400:
                return {
                    "ok": False,
                    "configured": True,
                    "latency_ms": latency_ms,
                    "note": f"HTTP {r.status_code}",
                }
            return {
                "ok": True,
                "configured": True,
                "latency_ms": latency_ms,
                "base_url": self.base_url,
                "model": self.model,
            }
        except Exception as e:
            return {
                "ok": False,
                "configured": True,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "note": str(e)[:200],
                "base_url": self.base_url,
            }
