"""Platform agent — AI-booking style: create_openai_tools_agent + AgentExecutor."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from .. import config
from ..browser_tool import tools as browser_tools
from ..browser_tool.session import browser_runtime
from .debug_callback import AgentDebugCallback
from .tools import SeenTenderStore, build_langchain_tools, make_context, platform_from_url

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — автономный агент мониторинга тендерных площадок.

Твоя работа — вызывать инструменты браузера и дедупликации, точно следуя алгоритму. Генерация текста вторична.

**КРИТИЧЕСКИЕ ЗАКОНЫ (НАРУШЕНИЕ = ПРОВАЛ):**

**ЗАКОН №1: ИНСТРУМЕНТЫ — ЕДИНСТВЕННАЯ РЕАЛЬНОСТЬ.**
- Запрещено выдумывать URL тендеров, tender_id, содержимое документов или факт скачивания.
- Единственный источник правды — РЕАЛЬНЫЙ результат tool call (JSON с ok/message/...).
- Если tender_id не извлечён со страницы — вызови extract_tender_id (там есть hash-fallback).

**ЗАКОН №2: АЛГОРИТМ МОНИТОРИНГА.**
1. Убедись, что открыта платформа (navigate при необходимости).
2. Найди поиск/фильтры, введи keywords (type_text + press_key Enter или click_xy).
3. Извлеки ссылки на карточки из DOM: get_page_text и/или list_download_links.
4. Для каждого кандидата-URL:
   a) extract_tender_id(url)
   b) check_tender_seen — если уже seen, ПРОПУСТИ
   c) если new и лимит новых тендеров не исчерпан:
      - открой карточку (navigate или click_xy)
      - вкладка «Документы» / «Документы закупки» при необходимости
      - list_download_links → download_url (передавай suggested_name из текста ссылки)
      - mark_tender_seen после обработки карточки
5. DOM-first. screenshot — если UI непонятен (после screenshot в ответе будет VL-анализ).
6. Не качай десятки одинаковых редакций — достаточно последней + уникальных решений/протоколов.
7. При login/captcha/403 — сразу finish_platform_task(success=false) с объяснением.
8. Когда обработал нужное число новых тендеров или исчерпал выдачу — finish_platform_task(success=true, summary=...).

**ЗАКОН №3: ЗАВЕРШЕНИЕ.**
- Итог задачи только через finish_platform_task.
- В summary кратко: сколько новых, какие URL, что скачано / что не удалось.

**ДОСТУПНЫЕ ИНСТРУМЕНТЫ:**
navigate, screenshot, click_xy, type_text, press_key, scroll, wait,
get_page_text, list_download_links, download_url,
extract_tender_id, check_tender_seen, mark_tender_seen,
finish_platform_task.
"""


def _require_llm() -> None:
    if not config.AGENT_LLM_BASE_URL:
        raise RuntimeError("AGENT_LLM_BASE_URL is not set")
    if not config.AGENT_LLM_API_KEY:
        raise RuntimeError("AGENT_LLM_API_KEY is not set")


def _rel_data_path(path: str) -> str:
    try:
        p = Path(path).resolve()
        return str(p.relative_to(config.DATA_ROOT)).replace("\\", "/")
    except Exception:
        return path


def _build_llm() -> ChatOpenAI:
    _require_llm()
    return ChatOpenAI(
        model=config.AGENT_LLM_MODEL,
        api_key=config.AGENT_LLM_API_KEY,
        base_url=config.AGENT_LLM_BASE_URL,
        temperature=0.1,
        timeout=180,
    )


def build_agent_executor(tools: list, max_iterations: int) -> AgentExecutor:
    """Паттерн AI-booking: create_openai_tools_agent + AgentExecutor."""
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            (
                "human",
                (
                    "ТЕХНИЧЕСКАЯ ИНФОРМАЦИЯ:\n"
                    "- platform_url: {platform_url}\n"
                    "- platform: {platform}\n"
                    "- keywords: {keywords}\n"
                    "- max_new_tenders: {max_new_tenders}\n"
                    "- current_url: {current_url}\n\n"
                    "ЗАПРОС:\n"
                    "{input}"
                ),
            ),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )
    llm = _build_llm()
    agent = create_openai_tools_agent(llm, tools, prompt)
    callbacks = [AgentDebugCallback()] if config.AGENT_DEBUG_LOGS else []
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=max_iterations,
        return_intermediate_steps=True,
        handle_parsing_errors=True,
        callbacks=callbacks,
    )


async def run_platform_task(
    platform_url: str,
    keywords: str,
    max_new_tenders: int | None = None,
    max_steps: int | None = None,
    download_subdir: str | None = None,
) -> dict[str, Any]:
    """
    LangChain AgentExecutor (как AI-booking):
    - qwen-max tool-calling
    - VL внутри tool `screenshot` (qwen-vl-plus)
    - SQLite dedup
    """
    platform_url = (platform_url or "").strip()
    keywords = (keywords or "").strip()
    if not platform_url or not keywords:
        raise ValueError("platform_url and keywords are required")

    parsed = urlparse(platform_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("platform_url must be http(s)")

    max_new = max_new_tenders or config.PLATFORM_MAX_NEW_TENDERS
    max_steps = max_steps or config.PLATFORM_MAX_STEPS

    if download_subdir:
        safe = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in download_subdir
        )[:80]
        downloads = config.DATA_ROOT / "tenders" / safe
    else:
        plat = platform_from_url(platform_url).replace(".", "_")
        kw = "".join(c if c.isalnum() else "_" for c in keywords[:40])
        downloads = config.DATA_ROOT / "tenders" / f"platform-{plat}-{kw}"
    downloads.mkdir(parents=True, exist_ok=True)

    store = SeenTenderStore(config.SEEN_TENDERS_DB)

    user_input = (
        f"Перейди на платформу и найди НОВЫЕ тендеры по ключевым словам, "
        f"скачай документацию. Лимит новых: {max_new}. "
        f"В конце обязательно вызови finish_platform_task."
    )

    async with browser_runtime(downloads_dir=downloads) as rt:
        nav = await browser_tools.navigate(rt, platform_url)
        ctx = make_context(
            rt,
            store,
            platform_url,
            keywords,
            max_new,
            task_hint=user_input,
        )
        ctx.trace.append({"tool": "navigate", "args": {"url": platform_url}, "result": nav})

        tools = build_langchain_tools(ctx)
        executor = build_agent_executor(tools, max_iterations=max_steps)

        logger.info(
            "platform AgentExecutor start platform=%s keywords=%s max_iter=%s",
            ctx.platform,
            keywords,
            max_steps,
        )

        result = await executor.ainvoke(
            {
                "input": user_input,
                "platform_url": platform_url,
                "platform": ctx.platform,
                "keywords": keywords,
                "max_new_tenders": str(max_new),
                "current_url": rt.page.url,
            }
        )

        files = list(rt.downloaded_files)
        files_rel = [_rel_data_path(f) for f in files]

        output_text = (result.get("output") or "").strip()
        if ctx.done:
            final_success = ctx.final_success
            final_summary = ctx.final_summary or output_text
        else:
            final_summary = output_text or "Agent stopped without finish_platform_task"
            final_success = bool(files) or ctx.new_tenders_processed > 0

        # intermediate_steps → дополняем trace (если tool не писал сам)
        steps = result.get("intermediate_steps") or []
        for action, observation in steps:
            name = getattr(action, "tool", None) or str(action)
            tool_input = getattr(action, "tool_input", {}) or {}
            # уже пишем в tools._trace — здесь только если пусто
            if not any(t.get("tool") == name for t in ctx.trace[-3:]):
                ctx.trace.append(
                    {
                        "tool": name,
                        "args": tool_input if isinstance(tool_input, dict) else {"input": tool_input},
                        "result": observation if isinstance(observation, dict) else {"raw": str(observation)[:4000]},
                    }
                )

    return {
        "success": final_success,
        "summary": final_summary,
        "platform": ctx.platform,
        "platform_url": platform_url,
        "keywords": keywords,
        "new_tenders_processed": ctx.new_tenders_processed,
        "max_new_tenders": max_new,
        "processed_tenders": ctx.processed_tenders,
        "downloaded_files": files,
        "downloaded_files_rel": files_rel,
        "steps": len(ctx.trace),
        "trace": ctx.trace,
        "downloads_dir": str(downloads),
        "seen_tenders_db": str(config.SEEN_TENDERS_DB),
        "model": config.AGENT_LLM_MODEL,
        "vl_model": config.AGENT_VL_MODEL,
        "vl_enabled": config.AGENT_VL_ENABLED,
        "agent_framework": "langchain.AgentExecutor",
    }
