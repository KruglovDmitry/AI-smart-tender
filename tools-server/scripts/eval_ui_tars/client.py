"""OpenAI-compatible client for local UI-TARS vLLM (Mode A GROUNDING)."""

from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

_TOOLS = Path(__file__).resolve().parents[2]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

try:
    from app.core.vision.ui_tars.prompts import GROUNDING_PROMPT
except Exception:  # pragma: no cover — standalone fallback
    GROUNDING_PROMPT = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format

Action: ...

## Action Space
click(start_box='(x1,y1)')
not_found()

## Note
- Output Action only.
- If the target is NOT visible on the screenshot, output exactly: Action: not_found()
- Do not guess a random click when the element is absent.

## User Instruction
{instruction}
"""

DEFAULT_BASE = os.environ.get("UI_TARS_BASE_URL", "").rstrip("/") or "http://127.0.0.1:8000"
DEFAULT_MODEL = os.environ.get("UI_TARS_MODEL", "ui-tars")


class UiTarsClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE,
        model: str = DEFAULT_MODEL,
        timeout: float = 180.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def ground(self, image_png: bytes, instruction: str) -> dict[str, Any]:
        b64 = base64.b64encode(image_png).decode("ascii")
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
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 256,
        }
        t0 = time.perf_counter()
        try:
            r = httpx.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=self.timeout,
            )
        except Exception as e:
            return {
                "ok": False,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "content": "",
                "error": str(e),
            }
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code >= 400:
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "content": "",
                "error": f"HTTP {r.status_code}: {r.text[:500]}",
            }
        data = r.json()
        content = (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        )
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "content": content,
            "raw": data,
        }
