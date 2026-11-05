"""Явная загрузка инструкций формата для tool-calling моделей."""
from pathlib import Path


def document_skill(file_type: str) -> dict:
    # Реестр фиксирован: параметр никогда не превращается в произвольный путь.
    if file_type not in {"pdf", "spreadsheets", "docx"}:
        raise ValueError("Supported file_type: pdf, spreadsheets, docx")
    instructions = (Path(__file__).parent / file_type / "SKILL.md").read_text(encoding="utf-8")
    return {"file_type": file_type, "scope": "tools-server", "instructions": instructions}
