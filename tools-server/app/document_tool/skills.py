"""Явная загрузка инструкций формата для tool-calling моделей."""
from pathlib import Path


def document_skill(file_type: str) -> dict:
    # Реестр фиксирован: параметр никогда не превращается в произвольный путь.
    if file_type != "pdf":
        raise ValueError("Supported file_type: pdf")
    instructions = (Path(__file__).parent / "pdf" / "SKILL.md").read_text(encoding="utf-8")
    return {"file_type": "pdf", "scope": "tools-server", "instructions": instructions}
