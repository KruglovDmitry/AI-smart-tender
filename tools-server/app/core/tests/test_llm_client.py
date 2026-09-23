"""Unit tests for LLM client helpers (no network)."""

from __future__ import annotations

from app.core.llm.client import extract_json_object, message_text


def test_extract_json_plain() -> None:
    assert extract_json_object('{"found": true, "x": 10}')["x"] == 10


def test_extract_json_fenced() -> None:
    text = 'note\n```json\n{"a": 1}\n```\n'
    assert extract_json_object(text).get("a") == 1


def test_message_text_list() -> None:
    msg = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "x"}},
        ],
    }
    assert message_text(msg) == "hello"
