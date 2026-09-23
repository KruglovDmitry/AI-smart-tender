"""LLM client package."""

from .client import chat_completions, chat_text, extract_json_object

__all__ = ["chat_completions", "chat_text", "extract_json_object", "locate"]


def __getattr__(name: str):
    if name == "locate":
        from .vision import locate

        return locate
    raise AttributeError(name)
