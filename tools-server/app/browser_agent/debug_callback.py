"""LangChain callbacks: log agent reasoning, tool calls and results for local debug."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

from .. import config

logger = logging.getLogger("browser_agent.debug")

_MAX = 4000


def _clip(text: Any, limit: int = _MAX) -> str:
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    if len(s) > limit:
        return s[:limit] + f"...(+{len(s) - limit} chars)"
    return s


class AgentDebugCallback(AsyncCallbackHandler):
    """Пишет в лог каждый ход: текст LLM → tool → результат."""

    def __init__(self) -> None:
        super().__init__()
        self._step = 0

    @property
    def enabled(self) -> bool:
        return config.AGENT_DEBUG_LOGS

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        if not self.enabled:
            return
        self._step += 1
        logger.info("─── LLM turn #%s ───", self._step)

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        if not self.enabled:
            return
        for gen_list in response.generations or []:
            for gen in gen_list:
                msg = getattr(gen, "message", None)
                content = getattr(msg, "content", None) if msg is not None else getattr(gen, "text", "")
                if content:
                    logger.info("LLM text:\n%s", _clip(content, 2000))
                tool_calls = getattr(msg, "tool_calls", None) if msg is not None else None
                if tool_calls:
                    for tc in tool_calls:
                        name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "?")
                        args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
                        logger.info("LLM tool_call → %s(%s)", name, _clip(args, 1500))
                elif not content:
                    # fallback: raw generation text
                    raw = getattr(gen, "text", "") or ""
                    if raw:
                        logger.info("LLM raw:\n%s", _clip(raw, 2000))

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        if not self.enabled:
            return
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        logger.info("▶ tool %s\n  args: %s", name, _clip(input_str, 2000))

    async def on_tool_end(
        self,
        output: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        if not self.enabled:
            return
        logger.info("◀ tool result:\n%s", _clip(output, _MAX))

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        if not self.enabled:
            return
        logger.exception("✖ tool error: %s", error)

    async def on_agent_finish(self, finish: Any, *, run_id: UUID, **kwargs: Any) -> None:
        if not self.enabled:
            return
        logger.info("✓ agent finish: %s", _clip(getattr(finish, "return_values", finish), 2000))
