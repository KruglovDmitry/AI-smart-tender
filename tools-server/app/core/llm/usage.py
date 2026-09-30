"""Per-request token usage (primary tool-caller vs overview extractor)."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Bucket:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class UsageLedger:
    roles: dict[str, _Bucket] = field(default_factory=dict)

    def add(self, role: str, usage: dict[str, Any] | None) -> None:
        bucket = self.roles.setdefault(role or "primary", _Bucket())
        u = usage or {}
        bucket.calls += 1
        bucket.prompt_tokens += int(u.get("prompt_tokens") or 0)
        bucket.completion_tokens += int(u.get("completion_tokens") or 0)
        bucket.cached_tokens += cached_tokens(u)

    def snapshot(self) -> dict[str, Any]:
        def pack(name: str) -> dict[str, int]:
            b = self.roles.get(name) or _Bucket()
            return {
                "calls": b.calls,
                "prompt_tokens": b.prompt_tokens,
                "completion_tokens": b.completion_tokens,
                "cached_tokens": b.cached_tokens,
            }

        primary = pack("primary")
        overview = pack("overview")
        other_prompt = other_completion = other_cached = other_calls = 0
        for name, b in self.roles.items():
            if name in {"primary", "overview"}:
                continue
            other_calls += b.calls
            other_prompt += b.prompt_tokens
            other_completion += b.completion_tokens
            other_cached += b.cached_tokens
        total = {
            "calls": primary["calls"] + overview["calls"] + other_calls,
            "prompt_tokens": primary["prompt_tokens"]
            + overview["prompt_tokens"]
            + other_prompt,
            "completion_tokens": primary["completion_tokens"]
            + overview["completion_tokens"]
            + other_completion,
            "cached_tokens": primary["cached_tokens"]
            + overview["cached_tokens"]
            + other_cached,
        }
        return {"primary": primary, "overview": overview, "total": total}


_ledger: ContextVar[UsageLedger | None] = ContextVar("llm_usage_ledger", default=None)
_role: ContextVar[str] = ContextVar("llm_usage_role", default="primary")


def cached_tokens(usage: dict[str, Any] | None) -> int:
    """DeepSeek prompt_cache_hit_tokens, else OpenAI/DashScope cached_tokens, else 0."""
    if not usage:
        return 0
    if usage.get("prompt_cache_hit_tokens") is not None:
        try:
            return int(usage.get("prompt_cache_hit_tokens") or 0)
        except (TypeError, ValueError):
            return 0
    details = usage.get("prompt_tokens_details") or {}
    if isinstance(details, dict) and details.get("cached_tokens") is not None:
        try:
            return int(details.get("cached_tokens") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def bind_usage() -> UsageLedger:
    """Start a fresh ledger for the current asyncio task."""
    ledger = UsageLedger()
    _ledger.set(ledger)
    _role.set("primary")
    return ledger


def current_usage() -> dict[str, Any]:
    ledger = _ledger.get()
    if ledger is None:
        empty = {
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
        }
        return {"primary": dict(empty), "overview": dict(empty), "total": dict(empty)}
    return ledger.snapshot()


@contextmanager
def usage_role(role: str):
    token = _role.set(role)
    try:
        yield
    finally:
        _role.reset(token)


def record_usage(usage: dict[str, Any] | None, *, role: str | None = None) -> None:
    ledger = _ledger.get()
    if ledger is None:
        return
    ledger.add(role or _role.get() or "primary", usage)


def usage_from_response(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    usage = data.get("usage")
    return usage if isinstance(usage, dict) else {}


def usage_from_ai_message(message: Any) -> dict[str, Any]:
    """LangChain AIMessage → OpenAI-like usage dict."""
    resp = getattr(message, "response_metadata", None) or {}
    if not isinstance(resp, dict):
        resp = {}
    raw = resp.get("token_usage") or resp.get("usage") or {}
    if not isinstance(raw, dict):
        raw = {}
    meta = getattr(message, "usage_metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    prompt = raw.get("prompt_tokens")
    if prompt is None:
        prompt = meta.get("input_tokens") or 0
    completion = raw.get("completion_tokens")
    if completion is None:
        completion = meta.get("output_tokens") or 0
    out: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
    }
    if raw.get("prompt_cache_hit_tokens") is not None:
        out["prompt_cache_hit_tokens"] = raw.get("prompt_cache_hit_tokens")
    details = raw.get("prompt_tokens_details")
    if isinstance(details, dict):
        out["prompt_tokens_details"] = details
    elif meta.get("input_token_details"):
        cache_read = (meta.get("input_token_details") or {}).get("cache_read")
        if cache_read is not None:
            out["prompt_tokens_details"] = {"cached_tokens": cache_read}
    return out
