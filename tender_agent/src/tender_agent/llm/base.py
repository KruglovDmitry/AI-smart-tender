from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    async def next_action(self, task: dict, observation: dict) -> dict:
        """Одно следующее действие в браузере: tool, ref, text, submit, direction, url, empty, thought."""

    async def read_results(self, task: dict, observation: dict) -> dict:
        """Записи выдачи с текущей страницы: {"records": [...]} дословными цитатами."""
