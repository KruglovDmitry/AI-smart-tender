"""Unit tests for LLM client helpers (no network)."""

from __future__ import annotations

from app.core.llm.client import extract_json_object, message_text
from app.core.llm.usage import bind_usage, cached_tokens, current_usage, record_usage


def test_extract_json_plain() -> None:
    assert extract_json_object('{"found": true, "x": 10}')["x"] == 10


def test_extract_json_fenced() -> None:
    text = 'note\n```json\n{"a": 1}\n```\n'
    assert extract_json_object(text).get("a") == 1


def test_cached_tokens_deepseek_and_openai() -> None:
    assert cached_tokens({"prompt_cache_hit_tokens": 12}) == 12
    assert cached_tokens({"prompt_tokens_details": {"cached_tokens": 7}}) == 7
    assert cached_tokens({"prompt_tokens": 3}) == 0


def test_usage_ledger_roles() -> None:
    bind_usage()
    record_usage({"prompt_tokens": 10, "completion_tokens": 2}, role="primary")
    record_usage(
        {"prompt_tokens": 4, "completion_tokens": 1, "prompt_cache_hit_tokens": 3},
        role="overview",
    )
    snap = current_usage()
    assert snap["primary"]["prompt_tokens"] == 10
    assert snap["overview"]["cached_tokens"] == 3
    assert snap["total"]["calls"] == 2
    assert snap["total"]["prompt_tokens"] == 14


def test_message_text_list() -> None:
    msg = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "x"}},
        ],
    }
    assert message_text(msg) == "hello"
