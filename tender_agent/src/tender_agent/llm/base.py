from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    async def decide(self, task: dict, observation: dict) -> dict:
        """Один следующий шаг: tool, element_ref, value, expected."""


class VisionProvider(Protocol):
    async def analyze(self, image: bytes, question: str) -> str:
        """Разбор скриншота. В текущей версии агент этот вызов не делает."""


async def visual_hint(provider: VisionProvider | None, image: bytes, question: str) -> str | None:
    if provider is None:
        return None
    return await provider.analyze(image, question)
