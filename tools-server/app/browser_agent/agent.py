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
from .tools import SeenTenderStore, build_langchain_tools, make_context, platform_from_url

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — автономный агент мониторинга тендерных площадок.

Твоя работа — вызывать инструменты браузера и дедупликации. Генерация текста вторична.

**КРИТИЧЕСКИЕ ЗАКОНЫ (НАРУШЕНИЕ = ПРОВАЛ):**

**ЗАКОН №1: ИНСТРУМЕНТЫ — ЕДИНСТВЕННАЯ РЕАЛЬНОСТЬ.**
- Запрещено выдумывать URL тендеров, tender_id, содержимое документов или факт скачивания.
- Источник правды — РЕАЛЬНЫЙ результат tool call (JSON с ok/message/url/...).
- finish_platform_task — ТОЛЬКО tool call (не текстом). Вызывай ОДИН раз, когда задача реально завершена.
- success=true только если критерии подтверждены текущим URL / скачанными файлами / processed_tenders.
- Запрещён ранний finish на главной/лендинге, если задача — поиск, карточка или документы.

**ЗАКОН №2: АЛГОРИТМ МОНИТОРИНГА.**
1. Убедись, что открыта целевая платформа (navigate при необходимости на platform_url).
2. Поиск по keywords — через UI площадки (не хардкодь чужие URL-шаблоны):
   a) Найди поле поиска на странице: screenshot → type_text ОБЯЗАТЕЛЬНО с x,y внутри viewport → submit/Enter.
      type_text без x,y ЗАПРЕЩЁН.
   b) Если координаты плохие — eval_js: найти видимое поле поиска, заполнить keywords, отправить форму.
   c) Успех поиска — только после screenshot (VL): на кадре видна выдача/список по запросу,
      не главная и не пустая форма. Иначе НЕ finish(success=true); повтори поиск.
3. Кандидаты карточек:
   - Собери ссылки на закупки из выдачи через get_page_text и/или eval_js (в порядке сверху вниз).
   - Открывай только карточки/извещения этой же площадки; пропускай javascript:, mailto:, служебные
     отчёты/статистику/футер и явные заглушки.
   - Если после navigate 404 / нет документов / list_download_links=0 — НЕ finish; следующий кандидат.
4. Для каждого кандидата:
   a) extract_tender_id(url)
   b) check_tender_seen — если seen, ПРОПУСТИ
   c) если new и лимит не исчерпан:
      - navigate на карточку → screenshot (VL): это карточка закупки, не ошибка/капча
      - открой раздел документов площадки при необходимости → screenshot (VL) перед скачиванием
      - list_download_links → download_url ТОЛЬКО kind=file
      - не качай навигацию, футер и служебные ссылки
      - если files пусто — screenshot+VL или click_xy(expect_download=true); иначе следующий тендер
      - mark_tender_seen после обработки
5. VL-верификация (screenshot обязателен на чекпоинтах):
   - После: открытие площадки, поиск, переход на карточку, открытие документов, сомнительный клик.
   - Сверяй VL-описание с ожидаемым состоянием; при расхождении — исправь шаг, не иди дальше «вслепую».
   - Координаты для type_text/click_xy — только из свежего screenshot (x < width, y < height).
   - eval_js/get_page_text дополняют VL, но не заменяют проверку ключевых переходов.
6. Не качай десятки одинаковых редакций — последняя версия + уникальные протоколы/решения.
7. login/captcha/403 — finish_platform_task(success=false).
8. finish_platform_task(success=true) только после реальной обработки лимита новых ИЛИ исчерпания валидных кандидатов
   (краткий summary: URL, tender_id, файлы — только из tool results, без плейсхолдеров).
   Перед финальным success=true — screenshot (VL), если ещё не делал на последнем состоянии.

**ЗАКОН №3: ЗАВЕРШЕНИЕ.**
- Итог только через finish_platform_task.
- В summary: сколько новых, какие URL, что скачано / почему не удалось.

**ДОСТУПНЫЕ ИНСТРУМЕНТЫ:**
navigate, screenshot, click_xy, type_text, press_key, scroll, wait,
get_page_text, list_download_links, download_url, eval_js,
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
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=max_iterations,
        return_intermediate_steps=True,
        handle_parsing_errors=True,
    )


async def run_platform_task(
    platform_url: str,
    keywords: str,
    max_new_tenders: int | None = None,
    max_steps: int | None = None,
    download_subdir: str | None = None,
    instruction: str | None = None,
) -> dict[str, Any]:
    """
    LangChain AgentExecutor (как AI-booking):
    - qwen-max tool-calling
    - VL внутри tool `screenshot` (qwen-vl-plus)
    - SQLite dedup
    - instruction — опциональный override user-запроса (для отладочных скриптов)
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

    user_input = (instruction or "").strip() or (
        f"Перейди на платформу и найди НОВЫЕ тендеры по ключевым словам, "
        f"скачай документацию. Лимит новых: {max_new}. "
        f"В конце обязательно вызови finish_platform_task."
    )
    if "finish_platform_task" not in user_input:
        user_input += " В конце обязательно вызови finish_platform_task."

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
