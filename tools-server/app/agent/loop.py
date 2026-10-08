"""Platform agent tool-calling loop."""

from __future__ import annotations

import json
import logging
import time
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
from ..core.llm.usage import bind_usage, current_usage, record_usage, usage_from_ai_message
from .context import PlatformAgentContext, platform_notes_digest
from .logging import current_agent_log_path, log_messages, setup_agent_file_logging
from .prompt import SYSTEM_PROMPT_PLATFORM, SYSTEM_PROMPT_TENDER_DOWNLOAD
from .tools import (
    SeenTenderStore,
    build_langchain_tools,
    make_context,
    normalize_tools_mode,
    platform_from_url,
    tool_names_for_mode,
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
    kwargs: dict[str, Any] = {}
    model = config.AGENT_PRIMARY_MODEL
    base = (config.AGENT_LLM_BASE_URL or "").lower()
    if "deepseek" in model.lower() or "deepseek.com" in base:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    return ChatOpenAI(
        model=model,
        api_key=config.AGENT_LLM_API_KEY,
        base_url=config.AGENT_LLM_BASE_URL,
        temperature=0.1,
        timeout=180,
        **kwargs,
    )


def _progress_digest(ctx: PlatformAgentContext) -> str:
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


def _compact_messages_after_tender(
    messages: list[Any], ctx: PlatformAgentContext
) -> list[Any]:
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
    """ChatOpenAI + tools loop (text primary, DOM/adapter tools)."""
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
                f"- primary_model: {config.AGENT_PRIMARY_MODEL}\n"
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
        record_usage(usage_from_ai_message(ai), role="primary")
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
            if step_i == 0 or step_i % 10 == 0:
                log_messages(step_i, messages, label="after_llm")

        tool_calls = getattr(ai, "tool_calls", None) or []
        if not tool_calls:
            logger.info("tool loop: no tool_calls at step=%s", step_i)
            if config.AGENT_DEBUG_LOGS:
                log_messages(step_i, messages, label="final")
            break

        names = [tc.get("name") for tc in tool_calls]
        logger.info("LLM step=%s tool_calls=%s", step_i, names)

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
                observation = await _ainvoke_tool(
                    tool, args if isinstance(args, dict) else {}
                )
                if config.AGENT_DEBUG_LOGS:
                    logger.info(
                        "tool_result step=%s name=%s observation=%s",
                        step_i,
                        name,
                        observation[:2000],
                    )
            messages.append(ToolMessage(content=observation, tool_call_id=tc_id))
            if name in {"mark_tender_seen", "mark_processed"}:
                marked_seen = True
            if ctx.done:
                break

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
    """Tool-calling loop (platform = DOM + adapters)."""
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
    bind_usage()
    t0 = time.perf_counter()
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + session_name[:40]
    )
    agent_log_path = setup_agent_file_logging(run_tag=f"{session_name}-platform")

    if (keywords or "").strip().lower() in {"tender-download", "single", "one"}:
        user_input = (instruction or "").strip() or (
            f"Одна закупка: {platform_url}. "
            f"open_tender(card_url) → save_overview → list_tender_documents → "
            f"download_document → mark_processed → finish. "
            f"Если list пуст — dom_snapshot → click_element по вкладке документов."
        )
        system_prompt = SYSTEM_PROMPT_TENDER_DOWNLOAD
    else:
        user_input = (instruction or "").strip() or (
            f"Площадка {platform_url}. Keywords: «{keywords}». Лимит новых: {max_new}. "
            f"Happy-path: open_platform_search → list_new_cards → open_tender → "
            f"save_overview → list_tender_documents → download_document → mark_processed → "
            f"finish. DOM: dom_snapshot → click/fill_element."
        )
        system_prompt = SYSTEM_PROMPT_PLATFORM
    if "finish" not in user_input.lower():
        user_input += " В конце вызови finish."

    logger.info(
        "platform agent EIS_TEST_NO_DOCS_ROUTE=%s",
        getattr(config, "EIS_TEST_NO_DOCS_ROUTE", False),
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
            downloads_root=downloads,
        )
        ctx.trace.append(
            {"tool": "navigate", "args": {"url": platform_url}, "result": nav}
        )

        tools = build_langchain_tools(ctx, mode=mode)

        logger.info(
            "platform loop start platform=%s keywords=%s max_iter=%s "
            "model=%s tools_mode=%s tools=%s",
            ctx.platform,
            keywords,
            max_steps,
            config.AGENT_PRIMARY_MODEL,
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
            final_summary = output_text or "Agent stopped without finish"
            final_success = bool(files) or ctx.new_tenders_processed > 0

    result = {
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
        "eis_test_no_docs_route": bool(
            getattr(config, "EIS_TEST_NO_DOCS_ROUTE", False)
        ),
        "agent_framework": "langchain.tool_loop",
        "tools_mode": mode,
        "tools": tool_names_for_mode(),
        "agent_log_path": str(agent_log_path or current_agent_log_path() or ""),
        "run_id": run_id,
        "max_steps": max_steps,
        "usage": current_usage(),
        "wall_time_s": round(time.perf_counter() - t0, 1),
    }
    debug_path = _write_debug_json(run_id, result)
    if debug_path is not None:
        result["debug_json"] = str(debug_path)
    return result


def _write_debug_json(run_id: str, payload: dict[str, Any]) -> Path | None:
    if not config.AGENT_DEBUG_LOGS:
        return None
    try:
        config.AGENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = config.AGENT_LOG_DIR / f"{run_id}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path
    except Exception as e:
        logger.warning("debug json write failed: %s", e)
        return None
