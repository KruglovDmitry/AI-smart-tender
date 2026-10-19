from __future__ import annotations

import re

from ..agent.orchestrator import execute
from ..api.schemas import TaskResult
from ..browser.runtime import BrowserRuntime
from ..config import Settings
from ..llm.base import LLMClient
from ..llm.provider import OpenAICompatibleClient
from ..observability.trace import RunTrace, current_trace
from ..storage.repository import Repository
from ..storage.tenders import TenderStore


def _store(settings: Settings, text: str, result: TaskResult, trace: RunTrace | None) -> None:
    """Все найденные закупки — в базу; в ответе отмечаем, какие появились впервые."""
    if not settings.store_enabled or result.task is None:
        return
    try:
        store = TenderStore(settings.db_url or settings.data_dir / "_agent" / "agent.sqlite3")
        saved = store.save_run(text, result, str(trace.dir) if trace else None)
        keys = {key for key in store.keys_new_in_run(saved["run_id"])}
        for tender in result.results:
            tender.is_new = (tender.id or tender.url) in keys
        result.stats["new_tenders"] = saved["new"]
        result.stats["db_run_id"] = saved["run_id"]
    except Exception as exc:  # база не должна ронять поиск
        result.errors.append(f"Не удалось сохранить в базу: {exc}")


async def run_task(
    text: str,
    *,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    runtime: BrowserRuntime | None = None,
    repo: Repository | None = None,
    download: bool | None = None,
) -> TaskResult:
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    repo = repo or Repository(settings.data_dir / "_agent" / "agent.sqlite3")
    if llm is None:
        client = OpenAICompatibleClient(settings)
        llm = client if client.configured else None
    trace = None
    if settings.trace_enabled:
        url = re.search(r"https?://[^\s,;»\"]+", text)
        trace = RunTrace(settings.data_dir / "_agent" / "runs", url.group(0) if url else "", text,
                         settings.trace_screenshots)
    token = current_trace.set(trace)
    result: TaskResult | None = None
    try:
        if runtime is not None:
            result = await execute(text, settings=settings, runtime=runtime, repo=repo, llm=llm, download=download)
        else:
            async with BrowserRuntime(settings, settings.data_dir / "_browser") as owned:
                result = await execute(text, settings=settings, runtime=owned, repo=repo, llm=llm, download=download)
        _store(settings, text, result, trace)
        return result
    except Exception as exc:
        result = TaskResult(status="failed", errors=[f"{type(exc).__name__}: {exc}"])
        raise
    finally:
        current_trace.reset(token)
        if trace is not None and result is not None:
            trace.finish(result)
