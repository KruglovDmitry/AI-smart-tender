from __future__ import annotations

from ..api.schemas import TaskSpec
from ..tenders.discovery import pick_section
from ..tenders.models import Observation
from .state import Action, Progress


def decide(obs: Observation, progress: Progress, task: TaskSpec) -> Action:
    if obs.page_kind in {"login", "captcha"}:
        return Action(tool="ask_user", expected="user")
    if obs.page_kind == "error":
        return Action(tool="finish", expected="error", note=obs.title or "страница ошибки")
    keyword = task.keywords[0] if task.keywords else ""
    if keyword and not progress.keyword_applied and obs.search_ref:
        return Action(
            tool="fill",
            element_ref=obs.search_ref,
            value=keyword,
            submit_ref=obs.submit_ref,
            expected="changed",
        )
    if task.sort_by and not progress.sort_applied and obs.sort_ref:
        return Action(tool="click", element_ref=obs.sort_ref, expected="changed", note="sort")
    if obs.cards and obs.signature not in progress.extracted:
        return Action(tool="extract", expected="cards")
    need_more = bool(task.sort_by) or progress.accepted < task.limit
    if need_more and obs.next_ref and progress.pages < task.max_pages:
        return Action(tool="click", element_ref=obs.next_ref, expected="changed", note="next")
    section = pick_section(obs.elements)
    if section and obs.page_kind in {"home", "unknown"} and not obs.cards:
        return Action(tool="click", element_ref=section.ref, expected="changed", note="section")
    if obs.page_kind == "unknown":
        return Action(tool="consult_llm", expected="changed")
    return Action(tool="finish", expected="done")
