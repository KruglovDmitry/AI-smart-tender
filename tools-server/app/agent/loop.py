"""Platform agent tool-calling loop."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from .. import config
from ..core.browser import primitives as browser_tools
from ..core.browser.session import browser_runtime
from .context import PlatformAgentContext, platform_notes_digest
from .logging import current_agent_log_path, log_messages, setup_agent_file_logging
from .prompt import SYSTEM_PROMPT_BROWSER, SYSTEM_PROMPT_PLATFORM
from .tools import (
    SeenTenderStore,
    build_langchain_tools,
    make_context,
    normalize_tools_mode,
    platform_from_url,
)

logger = logging.getLogger(__name__)


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
        model=config.AGENT_PRIMARY_MODEL,
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
            "save_overview → documents → download → mark_processed."
        )
    else:
        lines.append("Лимит новых исчерпан → finish(success=true).")
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
    if not getattr(ctx, "inject_screenshots", False):
        return None
    if not (config.AGENT_PRIMARY_MULTIMODAL and config.AGENT_VL_ENABLED):
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
    sys_text = system_prompt or SYSTEM_PROMPT_PLATFORM

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
            if name in {"mark_tender_seen", "mark_processed"}:
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
                        config.AGENT_PRIMARY_MODEL,
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
    Tool-calling loop.
    tools_mode: platform (default) | browser (ablation). full — rejected.
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
        tools_mode
        if tools_mode is not None
        else getattr(config, "PLATFORM_AGENT_MODE", "platform")
    )

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
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + session_name[:40]
    )
    agent_log_path = setup_agent_file_logging(run_tag=f"{session_name}-{mode}")

    if mode == "browser":
        user_input = (instruction or "").strip() or (
            f"Режим browser-only (абляция). Найди и обработай до {max_new} закупок по «{keywords}»: "
            f"поиск на UI, navigate(exact href), скачай документы, finish."
        )
        system_prompt = SYSTEM_PROMPT_BROWSER
    else:
        user_input = (instruction or "").strip() or (
            f"Площадка {platform_url}. Keywords: «{keywords}». Лимит новых: {max_new}. "
            f"Happy-path: open_platform_search → list_new_cards → open_tender → "
            f"save_overview → list_tender_documents → download_document → mark_processed → "
            f"finish. DOM: dom_snapshot → click/fill_element. "
            f"Vision: click_on_screen / inspect_screen."
        )
        system_prompt = SYSTEM_PROMPT_PLATFORM
    if "finish" not in user_input.lower():
        user_input += " В конце вызови finish."

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
        ctx.vision_run_id = run_id
        ctx.inject_screenshots = mode == "browser" and config.AGENT_PRIMARY_MULTIMODAL
        ctx.trace.append(
            {"tool": "navigate", "args": {"url": platform_url}, "result": nav}
        )

        tools = build_langchain_tools(ctx, mode=mode)
        inject_shots = mode == "browser" and config.AGENT_PRIMARY_MULTIMODAL

        logger.info(
            "platform loop start platform=%s keywords=%s max_iter=%s "
            "model=%s tools_mode=%s inject_shots=%s tools=%s",
            ctx.platform,
            keywords,
            max_steps,
            config.AGENT_PRIMARY_MODEL,
            mode,
            inject_shots,
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
            final_summary = output_text or "Agent stopped without finish"
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
        "model": config.AGENT_PRIMARY_MODEL,
        "vl_model": config.AGENT_VL_MODEL,
        "vl_enabled": config.AGENT_VL_ENABLED,
        "vision_backend": config.AGENT_VISION_BACKEND,
        "vision_run_id": run_id,
        "vl_mode": "click_on_screen" if mode == "platform" else "inline_multimodal",
        "agent_framework": "langchain.tool_loop",
        "tools_mode": mode,
        "tools": [t.name for t in tools],
        "agent_log_path": str(agent_log_path or current_agent_log_path() or ""),
        "max_steps": max_steps,
    }
