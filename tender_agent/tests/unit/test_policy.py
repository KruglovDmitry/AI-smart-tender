import asyncio

from tender_agent.agent.planner import consult
from tender_agent.agent.policy import decide
from tender_agent.agent.recovery import should_stop_repeat
from tender_agent.agent.state import Action, Progress
from tender_agent.api.schemas import TaskSpec
from tender_agent.tenders.models import Element, Observation, RawCard


def _task(**kwargs: object) -> TaskSpec:
    data = {"url": "https://example.com", "topic": "сервер", "keywords": ["сервер"], "limit": 5}
    data.update(kwargs)
    return TaskSpec(**data)


def test_policy_order() -> None:
    task = _task(sort_by="published_at")
    assert decide(Observation(url="https://example.com", page_kind="login"), Progress(), task).tool == "ask_user"
    assert decide(Observation(url="https://example.com", page_kind="captcha"), Progress(), task).tool == "ask_user"
    assert decide(Observation(url="https://example.com", page_kind="error", title="404"), Progress(), task).expected == "error"

    search = Observation(url="https://example.com/search", page_kind="search", search_ref=3, submit_ref=4)
    filled = decide(search, Progress(), task)
    assert filled.tool == "fill" and filled.value == "сервер"

    progress = Progress(keyword_applied=True)
    listed = Observation(
        url="https://example.com/tenders",
        page_kind="tender_list",
        sort_ref=8,
        cards=[RawCard(title="Сервер", href="https://example.com/tenders/1")],
        signature="a",
    )
    assert decide(listed, progress, task).note == "sort"
    progress.sort_applied = True
    assert decide(listed, progress, task).tool == "extract"
    progress.extracted.append("a")
    listed.next_ref = 9
    assert decide(listed, progress, task).note == "next"
    assert decide(Observation(url="https://example.com/x", page_kind="unknown"), progress, task).tool == "consult_llm"


def test_repeat_stops_after_retries() -> None:
    progress = Progress()
    action = Action(tool="click", element_ref=1, note="next")
    assert should_stop_repeat(progress, action, "same", 2) is False
    progress.failures["click:1:next:same"] = 2
    assert should_stop_repeat(progress, action, "same", 2) is True


def test_consult_rejects_unknown_ref() -> None:
    class Bad:
        async def decide(self, task: dict, observation: dict) -> dict:
            return {"tool": "click", "element_ref": 99}

    obs = Observation(url="https://example.com", elements=[Element(ref=1, name="Продолжить")])
    action = asyncio.run(consult(Bad(), _task(), obs))
    assert action is None
