"""
Интеграционные сценарии browser_agent (живой LLM + браузер + ЕИС).

По умолчанию НЕ гоняются обычным pytest (маркер integration).

Из каталога tools-server:
  py -m pytest app/browser_agent/tests -m integration -v
  py -m pytest app/browser_agent/tests -m integration -k scenario_1 -v
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest

from app import config
from app.browser_agent.agent import run_platform_task
from app.browser_agent.tests.scenarios import SCENARIOS

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

PLATFORM_URL = "https://zakupki.gov.ru/"
KEYWORDS = "канцтовары"


def _trace_urls(data: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for step in data.get("trace") or []:
        result = step.get("result") or {}
        if isinstance(result, dict) and result.get("url"):
            urls.append(str(result["url"]))
        # finish args sometimes only in message
    return urls


def _last_url(data: dict[str, Any]) -> str:
    urls = _trace_urls(data)
    return urls[-1] if urls else ""


def _save_debug(scenario_id: int, data: dict[str, Any]) -> Path:
    out = config.DATA_ROOT / f"debug-platform-s{scenario_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


@pytest.mark.parametrize(
    "scenario_id",
    sorted(SCENARIOS),
    ids=[SCENARIOS[i]["name"] for i in sorted(SCENARIOS)],
)
async def test_platform_scenario(scenario_id: int, require_agent_llm: None) -> None:
    cfg = SCENARIOS[scenario_id]
    data = await run_platform_task(
        platform_url=PLATFORM_URL,
        keywords=KEYWORDS,
        max_new_tenders=cfg["max_new_tenders"],
        max_steps=cfg["max_steps"],
        download_subdir=f"platform-s{scenario_id}",
        instruction=cfg["instruction"],
    )
    path = _save_debug(scenario_id, data)
    assert data.get("agent_framework") == "langchain.AgentExecutor"
    assert path.exists()

    url = _last_url(data)
    tools = [t.get("tool") for t in data.get("trace") or []]

    if scenario_id == 1:
        assert data.get("success") is True, data.get("summary")
        assert "zakupki.gov.ru" in url
        assert "get_page_text" in tools or "screenshot" in tools
        assert "finish_platform_task" in tools
        return

    if scenario_id == 2:
        # Строгая проверка: выдача, а не ложный success на home
        decoded = unquote(url)
        ok_url = "results.html" in url or "searchString" in decoded
        assert ok_url, f"expected search results URL, got {url!r}; summary={data.get('summary')!r}"
        assert data.get("success") is True, data.get("summary")
        return

    if scenario_id == 3:
        assert data.get("success") is True, data.get("summary")
        assert "notice" in url or "regNumber" in url or data.get("processed_tenders"), (
            f"expected tender card, got url={url!r} processed={data.get('processed_tenders')}"
        )
        assert "extract_tender_id" in tools or data.get("processed_tenders")
        return

    if scenario_id == 4:
        files = data.get("downloaded_files_rel") or data.get("downloaded_files") or []
        assert data.get("success") is True, data.get("summary")
        assert files, f"expected downloaded files, summary={data.get('summary')!r}"
        # не HTML-заглушки без расширения
        assert any(
            str(f).lower().endswith(ext)
            for f in files
            for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".7z", ".rar", ".rtf")
        ), files
        return

    pytest.fail(f"unhandled scenario {scenario_id}")
