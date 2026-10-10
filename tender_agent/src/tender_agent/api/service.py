from __future__ import annotations

from ..agent.orchestrator import execute
from ..api.schemas import TaskResult
from ..browser.runtime import BrowserRuntime
from ..config import Settings
from ..llm.base import LLMClient
from ..llm.provider import OpenAICompatibleClient
from ..storage.repository import Repository


async def run_task(
    text: str,
    *,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    runtime: BrowserRuntime | None = None,
    repo: Repository | None = None,
) -> TaskResult:
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    repo = repo or Repository(settings.data_dir / "_agent" / "agent.sqlite3")
    if llm is None:
        client = OpenAICompatibleClient(settings)
        llm = client if client.configured else None
    if runtime is not None:
        return await execute(text, settings=settings, runtime=runtime, repo=repo, llm=llm)
    async with BrowserRuntime(settings, settings.data_dir / "_browser") as owned:
        return await execute(text, settings=settings, runtime=owned, repo=repo, llm=llm)
