from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    async def next_action(self, task: dict, observation: dict) -> dict:
        """Одно следующее действие в браузере: tool, ref, text, submit, direction, url, empty, thought."""

    async def read_results(self, task: dict, observation: dict) -> dict:
        """Записи выдачи с текущей страницы: {"records": [...]} дословными цитатами."""

    async def read_card(self, task: dict, observation: dict) -> dict:
        """Поля, лоты и документы карточки закупки дословными цитатами и номерами элементов."""

    async def propose_notes(self, task: dict, observation: dict) -> dict:
        """Разбор трейса прогона: {"confirm": [id], "refute": [id], "new": [...]} для заметки площадки."""
