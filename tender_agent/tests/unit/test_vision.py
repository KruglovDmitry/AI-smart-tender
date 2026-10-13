import asyncio

from tender_agent.agent.orchestrator import _try_vision
from tender_agent.agent.state import Progress
from tender_agent.agent.vision import vision_goal
from tender_agent.api.schemas import TaskSpec
from tender_agent.config import Settings


def _task() -> TaskSpec:
    return TaskSpec(url="https://example.com", topic="сервер", keywords=["сервер"], limit=3, sort_by="published_at")


def test_goal_asks_for_search_and_sort_but_not_login() -> None:
    goal = vision_goal(_task(), "незнакомая страница", keyword_applied=False)
    assert "«сервер»" in goal
    assert "сортировк" in goal
    assert "Не входи в аккаунт" in goal


def test_vision_is_skipped_when_disabled() -> None:
    progress = Progress()
    errors: list[str] = []
    helped, spent = asyncio.run(_try_vision(None, Settings(vision_enabled=False), _task(), progress, "тест", errors))
    assert (helped, spent, progress.vision_calls, errors) == (False, 0.0, 0, [])
