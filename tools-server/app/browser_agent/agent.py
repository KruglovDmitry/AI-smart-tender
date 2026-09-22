"""Platform agent — multimodal tool-calling loop (one model sees screenshots)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from .. import config
from ..browser_tool import tools as browser_tools
from ..browser_tool.session import browser_runtime
from .logging import current_agent_log_path, log_messages, setup_agent_file_logging
from .tools import (
    SeenTenderStore,
    build_langchain_tools,
    make_context,
    normalize_tools_mode,
    platform_from_url,
)
from .tools._common import PlatformAgentContext, platform_notes_digest

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — автономный агент мониторинга тендерных площадок.

Твоя работа — вызывать инструменты браузера и дедупликации. Генерация текста вторична.
Площадка задана platform_url: изучай её UI сам, не опирайся на шаблоны URL одной конкретной системы.

Ты multimodal: после tool `screenshot` в следующем сообщении приходит ИЗОБРАЖЕНИЕ viewport.
Отдельной VL-модели нет — экран анализируешь ТЫ сам (что на странице, куда кликать, координаты).

**КРИТИЧЕСКИЕ ЗАКОНЫ (НАРУШЕНИЕ = ПРОВАЛ):**

**ЗАКОН №1: ИНСТРУМЕНТЫ — ЕДИНСТВЕННАЯ РЕАЛЬНОСТЬ.**
- Запрещено выдумывать URL тендеров, tender_id, содержимое документов или факт скачивания.
- Источник правды — РЕАЛЬНЫЙ результат tool call (JSON с ok/message/url/page_kind/...) и screenshot.
- Копируй exact href/tender_url из JSON. ЗАПРЕЩЕНЫ плейсхолдеры и «сборка» URL из id+шаблона.
- При page_kind=not_found / ok=false — вернись на results_url, не перебирай шаблоны.
- finish_platform_task — ТОЛЬКО tool call, один раз в конце.
- Имея href — navigate(href), не click_xy.

**ЗАКОН №2: АЛГОРИТМ.**
1. navigate(platform_url) при необходимости (не дублируй тот же URL).
2. Поиск: screenshot → type_text(x,y) → Enter. Успех — screenshot с выдачей
   (или get_page_text, если нужен только текст).
3. Выдача:
   a) collect_card_urls → filter_unseen_tenders(urls)
   b) new_count=0 → inspect_page_nav → navigate(suggested_next_url или next.href)
      ЗАПРЕЩЁН цикл scroll→screenshot→те же ссылки. Scroll — максимум 1 раз, если
      inspect_page_nav сказал, что пагинация ниже fold.
   c) Не меняй сортировку ради unseen, если это уводит в архив.
4. Для каждого new:
   a) navigate(exact tender_url) — смотри page_kind
   b) save_tender_overview; looks_stale → mark_tender_seen(count_toward_limit=false)
   c) иначе документы со страницы (list_download_links / вкладка) → download_url
   d) mark_tender_seen(count_toward_limit=true) → контекст сожмётся, NOTES останутся
5. Screenshot — только перед type_text/click_xy или при неясности UI.
   Не screenshot после каждого scroll. Для проверки текста — get_page_text.
6. click_xy: если changed=false — промах, не считай успехом; лучше navigate(href).
7. login/captcha → finish(success=false).
8. finish(success=true) после лимита засчитанных новых.

**ЗАКОН №3:** итог только через finish_platform_task.

**ИНСТРУМЕНТЫ:**
navigate, screenshot, click_xy, type_text, press_key, scroll, wait,
get_page_text, inspect_page_nav, list_download_links, download_url, collect_card_urls,
extract_tender_id, check_tender_seen, filter_unseen_tenders, save_tender_overview,
mark_tender_seen, finish_platform_task.
"""

SYSTEM_PROMPT_BROWSER = """Ты — агент, который управляет браузером ТОЛЬКО низкоуровневыми действиями.

Режим оценки: доменных helpers (collect_card_urls, filter_unseen, inspect_page_nav,
save_tender_overview, mark_tender_seen и т.п.) НЕТ. Всё делаешь сам через клики, ввод, scroll,
navigate по URL, которые видишь в get_page_text / list_download_links / screenshot.

Ты multimodal: после screenshot картинка приходит тебе в следующем сообщении.

**ПРАВИЛА:**
- Не выдумывай URL. Копируй href из tool results / текста страницы как есть.
- Имея href — navigate(href), не собирай путь из id.
- screenshot — перед type_text/click_xy; для проверки текста предпочитай get_page_text.
- click_xy: если changed=false — промах.
- finish_platform_task — один раз в конце (tool call).

**ЗАДАЧА (типично):** поиск по keywords на platform_url → открыть несколько карточек →
скачать документы (list_download_links / download_url или click expect_download) →
finish с summary (сколько обработал, пути файлов).

**ИНСТРУМЕНТЫ:**
navigate, screenshot, click_xy, type_text, press_key, scroll, wait,
get_page_text, list_download_links, download_url, finish_platform_task.
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


def _message_has_image(msg: Any) -> bool:
    content = getattr(msg, "content", None)
    if not isinstance(content, list):
        return False
    for part in content:
        if isinstance(part, dict) and part.get("type") in {"image_url", "image"}:
            return True
    return False


def _prune_old_screenshots(messages: list[Any]) -> None:
    """Оставляем только последний кадр в истории — экономия токенов."""
    image_idxs = [i for i, m in enumerate(messages) if _message_has_image(m)]
    if len(image_idxs) <= 1:
        return
    for i in image_idxs[:-1]:
        messages[i] = HumanMessage(
            content="[предыдущий screenshot удалён из контекста; ориентируйся на последний кадр]"
        )


def _progress_digest(ctx: PlatformAgentContext) -> str:
    """Краткий статус для LLM вместо полной истории tools по завершённым тендерам."""
    try:
        cur_url = ctx.rt.page.url
    except Exception:
        cur_url = ""
    lines = [
        "ПРОГРЕСС (сжатый контекст — детали прошлых tool-вызовов удалены):",
        f"- processed={ctx.new_tenders_processed}/{ctx.max_new_tenders}",
        f"- keywords={ctx.keywords!r}",
        f"- current_url={cur_url}",
    ]
    for t in ctx.processed_tenders[-8:]:
        tid = t.get("tender_id") or "?"
        tdir = t.get("tender_dir") or ""
        files: list[str] = []
        if tdir:
            try:
                p = Path(tdir)
                if p.is_dir():
                    files = sorted(
                        x.name
                        for x in p.iterdir()
                        if x.is_file() and x.name != "overview.json"
                    )[:8]
            except Exception:
                files = []
        files_s = ", ".join(files) if files else "(нет файлов / только overview)"
        lines.append(f"- DONE {tid}: files=[{files_s}]")
    remain = ctx.max_new_tenders - ctx.new_tenders_processed
    if remain > 0:
        lines.append(
            f"Осталось новых: {remain}. Продолжай: следующий new[] / пагинация / "
            "save_tender_overview → documents → download → mark_tender_seen."
        )
    else:
        lines.append("Лимит новых исчерпан → finish_platform_task(success=true).")
    lines.append("")
    lines.append(platform_notes_digest(ctx))
    return "\n".join(lines)


def _compact_messages_after_tender(messages: list[Any], ctx: PlatformAgentContext) -> list[Any]:
    """
    Оставляем system + исходный user-запрос + краткий прогресс.
    Убираем накопившиеся AI/Tool/screenshot по уже закрытым тендерам.
    """
    if len(messages) < 2:
        return messages
    system = messages[0]
    task = messages[1]
    digest = HumanMessage(content=_progress_digest(ctx))
    logger.info(
        "context compacted after tender: messages %s → 3 (digest processed=%s)",
        len(messages),
        ctx.new_tenders_processed,
    )
    return [system, task, digest]


def _screenshot_followup(ctx: PlatformAgentContext) -> HumanMessage | None:
    if not config.AGENT_VL_ENABLED:
        return None
    b64 = getattr(ctx.rt, "last_screenshot_b64", None)
    if not b64:
        return None
    meta = getattr(ctx.rt, "last_screenshot_meta", None) or {}
    w = meta.get("width") or config.BROWSER_VIEWPORT_WIDTH
    h = meta.get("height") or config.BROWSER_VIEWPORT_HEIGHT
    url = meta.get("url") or ctx.rt.page.url
    return HumanMessage(
        content=[
            {
                "type": "text",
                "text": (
                    f"Screenshot viewport {w}x{h}. URL: {url}. "
                    "Изображение ниже — текущий экран. "
                    "Оцени UI сам; для кликов/ввода используй координаты в пределах viewport; "
                    "затем продолжи через tool calls (не описывай картинку длинно)."
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            },
        ]
    )


async def _ainvoke_tool(tool: BaseTool, args: dict[str, Any]) -> str:
    try:
        result = await tool.ainvoke(args or {})
    except Exception as e:
        return f'{{"ok": false, "message": "tool error: {e}"}}'
    if isinstance(result, str):
        return result
    return str(result)


async def run_multimodal_tool_loop(
    ctx: PlatformAgentContext,
    tools: list[BaseTool],
    *,
    user_input: str,
    platform_url: str,
    keywords: str,
    max_new: int,
    max_steps: int,
    system_prompt: str | None = None,
) -> str:
    """
    Один multimodal ChatOpenAI + tools.
    screenshot не вызывает отдельный VL: картинка инжектится в messages.
    """
    llm = _build_llm().bind_tools(tools)
    tools_by_name = {t.name: t for t in tools}
    sys_text = system_prompt or SYSTEM_PROMPT

    messages: list[Any] = [
        SystemMessage(content=sys_text),
        HumanMessage(
            content=(
                "ТЕХНИЧЕСКАЯ ИНФОРМАЦИЯ:\n"
                f"- platform_url: {platform_url}\n"
                f"- platform: {ctx.platform}\n"
                f"- keywords: {keywords}\n"
                f"- max_new_tenders: {max_new}\n"
                f"- current_url: {ctx.rt.page.url}\n\n"
                f"ЗАПРОС:\n{user_input}"
            )
        ),
    ]

    last_text = ""
    for step_i in range(max_steps):
        if ctx.done:
            break

        ai: AIMessage = await llm.ainvoke(messages)
        messages.append(ai)

        raw_content = ai.content
        if isinstance(raw_content, str) and raw_content.strip():
            last_text = raw_content.strip()
        elif isinstance(raw_content, list):
            parts = [
                str(p.get("text") or "")
                for p in raw_content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            joined = "\n".join(x for x in parts if x).strip()
            if joined:
                last_text = joined

        if config.AGENT_DEBUG_LOGS:
            if last_text:
                logger.info("LLM step=%s text=%s", step_i, last_text)
            # Полный dump только редко — иначе лог раздувается мегабайтами
            if step_i == 0 or step_i % 10 == 0:
                log_messages(step_i, messages, label="after_llm")

        tool_calls = getattr(ai, "tool_calls", None) or []
        if not tool_calls:
            logger.info("multimodal loop: no tool_calls at step=%s", step_i)
            if config.AGENT_DEBUG_LOGS:
                log_messages(step_i, messages, label="final")
            break

        names = [tc.get("name") for tc in tool_calls]
        logger.info("LLM step=%s tool_calls=%s", step_i, names)

        saw_screenshot = False
        marked_seen = False
        for tc in tool_calls:
            name = tc.get("name") or ""
            args = tc.get("args") or {}
            tc_id = tc.get("id") or f"call_{step_i}_{name}"
            tool = tools_by_name.get(name)
            if tool is None:
                observation = f'{{"ok": false, "message": "unknown tool: {name}"}}'
            else:
                if config.AGENT_DEBUG_LOGS:
                    logger.info("tool_call step=%s name=%s args=%s", step_i, name, args)
                observation = await _ainvoke_tool(tool, args if isinstance(args, dict) else {})
                if config.AGENT_DEBUG_LOGS:
                    logger.info(
                        "tool_result step=%s name=%s observation=%s",
                        step_i,
                        name,
                        observation[:2000],
                    )
            messages.append(ToolMessage(content=observation, tool_call_id=tc_id))
            if name == "screenshot":
                saw_screenshot = True
            if name == "mark_tender_seen":
                marked_seen = True
            if ctx.done:
                break

        if saw_screenshot and not ctx.done:
            follow = _screenshot_followup(ctx)
            if follow is not None:
                _prune_old_screenshots(messages)
                messages.append(follow)
                if config.AGENT_DEBUG_LOGS:
                    logger.info(
                        "screenshot image attached to multimodal context (model=%s)",
                        config.AGENT_LLM_MODEL,
                    )

        # После закрытия тендера — в LLM только прогресс, без пачки download/list
        if marked_seen and not ctx.done:
            messages[:] = _compact_messages_after_tender(messages, ctx)

        if config.AGENT_DEBUG_LOGS and (marked_seen or step_i % 10 == 0):
            log_messages(step_i, messages, label="after_tools")

        if ctx.done:
            break

    if config.AGENT_DEBUG_LOGS:
        log_messages(-1, messages, label="end_of_loop")

    return ctx.final_summary or last_text


async def run_platform_task(
    platform_url: str,
    keywords: str,
    max_new_tenders: int | None = None,
    max_steps: int | None = None,
    download_subdir: str | None = None,
    instruction: str | None = None,
    tools_mode: str | None = None,
) -> dict[str, Any]:
    """
    Multimodal tool-calling loop:
    - AGENT_LLM_MODEL вызывает tools и сам смотрит screenshot (image в messages)
    - tools_mode: full (доменные helpers) | browser (только низкоуровневые browser tools)
    - instruction — опциональный override user-запроса
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
    mode = normalize_tools_mode(
        tools_mode if tools_mode is not None else getattr(config, "PLATFORM_AGENT_MODE", "full")
    )

    # Подсессия = имя площадки (host), напр. tenders/zakupki_gov_ru/<tender_id>/
    plat = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in platform_from_url(platform_url)
    )[:80] or "platform"
    if download_subdir:
        safe = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in download_subdir
        )[:80]
        session_name = safe
    else:
        session_name = plat
    downloads = config.DATA_ROOT / "tenders" / session_name
    downloads.mkdir(parents=True, exist_ok=True)

    store = SeenTenderStore(config.SEEN_TENDERS_DB)

    agent_log_path = setup_agent_file_logging(run_tag=f"{session_name}-{mode}")

    if mode == "browser":
        user_input = (instruction or "").strip() or (
            f"Режим browser-only. Найди и обработай до {max_new} закупок по «{keywords}»: "
            f"поиск на UI, открой карточки через navigate(exact href из get_page_text/"
            f"list_download_links), скачай документы, finish_platform_task. "
            f"Без доменных helpers — только клики/ввод/navigate/download."
        )
        system_prompt = SYSTEM_PROMPT_BROWSER
    else:
        user_input = (instruction or "").strip() or (
            f"Перейди на платформу и найди НОВЫЕ актуальные тендеры по ключевым словам, "
            f"скачай документацию. Лимит новых: {max_new}. "
            f"На выдаче: collect_card_urls → filter_unseen → при new=0 inspect_page_nav → "
            f"navigate(exact href / suggested_next_url). Не выдумывай URL. "
            f"looks_stale → mark_tender_seen(count_toward_limit=false). "
            f"Иначе save_tender_overview → документы со страницы → mark_tender_seen. "
            f"В конце finish_platform_task."
        )
        system_prompt = SYSTEM_PROMPT
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
            downloads_root=downloads,
        )
        ctx.trace.append({"tool": "navigate", "args": {"url": platform_url}, "result": nav})

        tools = build_langchain_tools(ctx, mode=mode)

        logger.info(
            "platform multimodal loop start platform=%s keywords=%s max_iter=%s "
            "model=%s tools_mode=%s tools=%s",
            ctx.platform,
            keywords,
            max_steps,
            config.AGENT_LLM_MODEL,
            mode,
            [t.name for t in tools],
        )

        output_text = await run_multimodal_tool_loop(
            ctx,
            tools,
            user_input=user_input,
            platform_url=platform_url,
            keywords=keywords,
            max_new=max_new,
            max_steps=max_steps,
            system_prompt=system_prompt,
        )

        files = list(rt.downloaded_files)
        files_rel = [_rel_data_path(f) for f in files]

        if ctx.done:
            final_success = ctx.final_success
            final_summary = ctx.final_summary or output_text
        else:
            final_summary = output_text or "Agent stopped without finish_platform_task"
            final_success = bool(files) or ctx.new_tenders_processed > 0

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
        "vl_model": config.AGENT_LLM_MODEL,
        "vl_enabled": config.AGENT_VL_ENABLED,
        "vl_mode": "inline_multimodal",
        "agent_framework": "langchain.multimodal_tool_loop",
        "tools_mode": mode,
        "tools": [t.name for t in tools],
        "agent_log_path": str(agent_log_path or current_agent_log_path() or ""),
        "max_steps": max_steps,
    }
