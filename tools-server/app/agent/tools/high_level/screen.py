"""Screen / vision tools: inspect, click_target, type, scroll, click_on_screen."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from ....core.browser.after_action import capture_before_state, describe_after_action
from ....core.vision import click_on_screen as vision_click
from ....core.vision import click_target as vision_click_target
from ....core.vision import get_vision_backend
from ....core.vision import inspect_screen as vision_inspect
from ....core.vision import scroll_screen as vision_scroll
from ....core.vision import type_into_target as vision_type_into
from ....core.vision.act import strip_coords
from ...context import PlatformAgentContext, to_json, trace
from .helpers import note_dom_blind, persist_download
from .schemas import (
    GoalInput,
    QuestionInput,
    ScrollInput,
    TargetIdInput,
    TypeIntoTargetInput,
)


def build_screen_tools(
    ctx: PlatformAgentContext, *, vision_mode: str
) -> dict[str, StructuredTool]:
    out: dict[str, StructuredTool] = {}

    async def inspect_screen(question: str | None = None) -> str:
        result = await vision_inspect(
            ctx.rt,
            question,
            mode=vision_mode,
        )
        ctx.screen_targets = {
            "url": result.get("_url") or result.get("url") or "",
            "fingerprint": result.get("_fingerprint") or "",
            "targets": list(result.get("_internal_targets") or []),
        }
        public = strip_coords(result)
        if public.get("answer") is None:
            public.pop("answer", None)
        trace(ctx, "inspect_screen", {"question": question}, public)
        return to_json(public)

    out["inspect_screen"] = StructuredTool.from_function(
        coroutine=inspect_screen,
        name="inspect_screen",
        description=(
            "Список кликабельных целей на экране (id+label+kind). "
            "Опционально question — ответ модели восприятия. "
            "Выбери цель из списка → click_target(id)."
        ),
        args_schema=QuestionInput,
    )

    async def click_target(target_id: str) -> str:
        result = await vision_click_target(
            ctx.rt,
            target_id,
            mode=vision_mode,
            screen_targets=ctx.screen_targets,
            grounding=get_vision_backend(),
            run_id=ctx.vision_run_id or None,
            platform=ctx.platform,
        )
        if result.get("download"):
            result["download"] = await persist_download(ctx, result.get("download"))
        public = strip_coords(result)
        note_dom_blind(ctx, "click_target", public)
        if public.get("ok") and public.get("changed"):
            ctx.screen_targets = {}
        trace(ctx, "click_target", {"target_id": target_id}, public)
        return to_json(public)

    out["click_target"] = StructuredTool.from_function(
        coroutine=click_target,
        name="click_target",
        description=(
            "Клик по цели из последнего inspect_screen (d… = DOM, v… = зрение). "
            "Не выдумывай id — только из списка."
        ),
        args_schema=TargetIdInput,
    )

    async def type_into_target(
        target_id: str, text: str, submit: bool = False
    ) -> str:
        result = await vision_type_into(
            ctx.rt,
            target_id,
            text,
            submit=submit,
            mode=vision_mode,
            screen_targets=ctx.screen_targets,
            grounding=get_vision_backend(),
            run_id=ctx.vision_run_id or None,
            platform=ctx.platform,
        )
        public = strip_coords(result)
        note_dom_blind(ctx, "type_into_target", public)
        if public.get("ok"):
            ctx.screen_targets = {}
        trace(
            ctx,
            "type_into_target",
            {"target_id": target_id, "text": text, "submit": submit},
            public,
        )
        return to_json(public)

    out["type_into_target"] = StructuredTool.from_function(
        coroutine=type_into_target,
        name="type_into_target",
        description="Клик по цели → Ctrl+A → ввод; submit=True → Enter.",
        args_schema=TypeIntoTargetInput,
    )

    async def scroll(direction: str = "down", amount: str = "screen") -> str:
        result = await vision_scroll(ctx.rt, direction=direction, amount=amount)
        ctx.screen_targets = {}
        trace(ctx, "scroll", {"direction": direction, "amount": amount}, result)
        return to_json(result)

    out["scroll"] = StructuredTool.from_function(
        coroutine=scroll,
        name="scroll",
        description=(
            "Прокрутка колесом (down|up, screen|half). После — inspect_screen снова."
        ),
        args_schema=ScrollInput,
    )

    async def click_on_screen(goal: str) -> str:
        before = await capture_before_state(ctx.rt, mode=vision_mode)
        backend = get_vision_backend()
        result = await vision_click(
            ctx.rt,
            goal,
            backend=backend,
            run_id=ctx.vision_run_id or None,
            platform=ctx.platform,
        )
        report = await describe_after_action(ctx.rt, before, mode=vision_mode)
        result = {
            **result,
            **{k: v for k, v in report.items() if k not in result or k == "changed"},
        }
        if "changed" in report:
            result["changed"] = report["changed"]
        public = strip_coords(result)
        note_dom_blind(ctx, "click_on_screen", public)
        if public.get("changed"):
            ctx.screen_targets = {}
        trace(ctx, "click_on_screen", {"goal": goal}, public)
        return to_json(public)

    out["click_on_screen"] = StructuredTool.from_function(
        coroutine=click_on_screen,
        name="click_on_screen",
        description=(
            "Запасной vision-клик по свободной формулировке goal. "
            "Предпочитай inspect_screen → click_target."
        ),
        args_schema=GoalInput,
    )

    return out
