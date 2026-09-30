"""DOM escape-hatch tools + navigate."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from ...core.browser import dom as browser_dom
from ...core.browser.after_action import capture_before_state, describe_after_action
from ...core.browser.primitives import navigate as core_navigate
from ..context import PlatformAgentContext, to_json, trace
from .schemas import DomIdInput, DomSnapshotInput, FillInput, NavigateInput


def build_dom_tools(ctx: PlatformAgentContext) -> dict[str, StructuredTool]:
    out: dict[str, StructuredTool] = {}

    async def dom_snapshot(query: str | None = None) -> str:
        result = await browser_dom.snapshot_for_agent(ctx.rt, query)
        trace(ctx, "dom_snapshot", {"query": query}, result)
        return to_json(result)

    out["dom_snapshot"] = StructuredTool.from_function(
        coroutine=dom_snapshot,
        name="dom_snapshot",
        description=(
            "DOM interactive elements (data-agent-id). "
            "query фильтрует; без query — до 150 (inputs→buttons→links)."
        ),
        args_schema=DomSnapshotInput,
    )

    async def click_element(el_id: int) -> str:
        before = await capture_before_state(ctx.rt, mode="dom")
        click_res = await browser_dom.click_by_id(ctx.rt, el_id)
        report = await describe_after_action(ctx.rt, before, mode="dom")
        result = {**click_res, **report}
        trace(ctx, "click_element", {"el_id": el_id}, result)
        return to_json(result)

    out["click_element"] = StructuredTool.from_function(
        coroutine=click_element,
        name="click_element",
        description="Клик по id из dom_snapshot (scroll into view).",
        args_schema=DomIdInput,
    )

    async def fill_element(el_id: int, text: str, submit: bool = False) -> str:
        before = await capture_before_state(ctx.rt, mode="dom") if submit else None
        result = await browser_dom.fill_by_id(ctx.rt, el_id, text, submit=submit)
        if submit and before is not None:
            report = await describe_after_action(ctx.rt, before, mode="dom")
            result = {**result, **report}
        trace(
            ctx,
            "fill_element",
            {"el_id": el_id, "text": text, "submit": submit},
            result,
        )
        return to_json(result)

    out["fill_element"] = StructuredTool.from_function(
        coroutine=fill_element,
        name="fill_element",
        description="Ввод в элемент по id; submit=True → Enter.",
        args_schema=FillInput,
    )

    async def navigate(url: str) -> str:
        result = await core_navigate(ctx.rt, url)
        trace(ctx, "navigate", {"url": url}, result)
        return to_json(result)

    out["navigate"] = StructuredTool.from_function(
        coroutine=navigate,
        name="navigate",
        description="Прямой переход по URL.",
        args_schema=NavigateInput,
    )

    return out
