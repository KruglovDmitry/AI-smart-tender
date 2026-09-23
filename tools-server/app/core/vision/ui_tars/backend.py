"""UiTarsBackend — Mode A GROUNDING only; never drives the browser."""

from __future__ import annotations

import logging

from ..base import GroundingCandidate, GroundingResult, InspectionResult
from ..scale import png_pixel_size
from .client import UiTarsClient
from .parser import parse_to_css

logger = logging.getLogger(__name__)


class UiTarsBackend:
    """
    VisionBackend using UI-TARS-1.5 via OpenAI-compatible vLLM.

    screenshot → GROUNDING → parser → CSS viewport coords.
    Browser actions stay in act.click_on_screen / agent tools.
    """

    name = "ui_tars"

    def __init__(self, client: UiTarsClient | None = None) -> None:
        self.client = client or UiTarsClient()

    async def ground(
        self,
        image_b64: str,
        target: str,
        viewport: tuple[int, int],
    ) -> GroundingResult:
        model = self.client.model
        if not self.client.configured:
            return GroundingResult(
                found=False,
                action="none",
                candidates=[],
                backend=self.name,
                model=model,
                note="UI_TARS_BASE_URL is not set",
                raw_response="",
            )
        if not image_b64:
            return GroundingResult(
                found=False,
                action="none",
                candidates=[],
                backend=self.name,
                model=model,
                note="empty image",
                raw_response="",
            )

        api = await self.client.ground(image_b64, target)
        latency = int(api.get("latency_ms") or 0)
        content = str(api.get("content") or "")
        if not api.get("ok"):
            return GroundingResult(
                found=False,
                action="none",
                candidates=[],
                backend=self.name,
                model=model,
                note=str(api.get("error") or "ui_tars request failed"),
                latency_ms=latency,
                raw_response=content,
            )

        img = png_pixel_size(image_b64) or (int(viewport[0]), int(viewport[1]))
        parsed = parse_to_css(
            content,
            image_width=img[0],
            image_height=img[1],
            viewport=viewport,
            model_type="qwen25vl",
        )
        action = str(parsed.get("action") or "none")
        if action == "not_found":
            return GroundingResult(
                found=False,
                action="not_found",
                candidates=[],
                backend=self.name,
                model=model,
                note=str(parsed.get("note") or "not_found"),
                latency_ms=latency,
                raw_response=content,
                raw={"parsed": parsed},
            )
        if action != "click" or parsed.get("x") is None:
            return GroundingResult(
                found=False,
                action="none",
                candidates=[],
                backend=self.name,
                model=model,
                note=str(parsed.get("note") or "parse failed"),
                latency_ms=latency,
                raw_response=content,
                raw={"parsed": parsed},
            )

        cand = GroundingCandidate(
            x=int(parsed["x"]),
            y=int(parsed["y"]),
            label="primary",
        )
        return GroundingResult(
            found=True,
            action="click",
            candidates=[cand],
            backend=self.name,
            model=model,
            note=str(parsed.get("note") or ""),
            latency_ms=latency,
            raw_response=content,
            raw={"parsed": parsed},
        )

    async def inspect(
        self,
        image_b64: str,
        question: str,
        viewport: tuple[int, int],
    ) -> InspectionResult:
        prompt = (
            "Look at the screenshot. Answer briefly in Russian (1–3 sentences). "
            f"No coordinates.\nQuestion: {(question or '')[:800]}"
        )
        messages = [
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
        ]
        api = await self.client.chat(messages, max_tokens=300)
        answer = str(api.get("content") or "")
        if not api.get("ok"):
            answer = f"inspect error: {api.get('error') or 'request failed'}"
            logger.warning("UiTarsBackend.inspect failed: %s", api.get("error"))
        return InspectionResult(
            answer=(answer or "").strip() or "(empty)",
            backend=self.name,
            model=self.client.model,
            latency_ms=int(api.get("latency_ms") or 0),
        )
