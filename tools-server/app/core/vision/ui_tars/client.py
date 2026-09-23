"""OpenAI-compatible vLLM client for UI-TARS-1.5."""

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
        self.base_url = (
            base_url
            or getattr(config, "UI_TARS_BASE_URL", None)
            or "http://10.127.0.41:8000"
        ).rstrip("/")
        self.model = model or getattr(config, "UI_TARS_MODEL", None) or "ui-tars"
        self.timeout = timeout
        self.api_key = api_key or getattr(config, "UI_TARS_API_KEY", "") or "EMPTY"

    def ground(self, image_b64: str, instruction: str) -> dict[str, Any]:
        """Mode A: GROUNDING prompt → raw model text + latency."""
        payload = {
            "model": self.model,
            "messages": [
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
            ],
            "temperature": 0,
            "max_tokens": 256,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        t0 = time.perf_counter()
        try:
            r = httpx.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout,
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
