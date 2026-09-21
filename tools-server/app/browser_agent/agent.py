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
from .logging import current_agent_log_path, setup_agent_file_logging
from .tools import SeenTenderStore, build_langchain_tools, make_context, platform_from_url
from .tools._common import PlatformAgentContext

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — автономный агент мониторинга тендерных площадок.

Твоя работа — вызывать инструменты браузера и дедупликации. Генерация текста вторична.

Ты multimodal: после tool `screenshot` в следующем сообщении приходит ИЗОБРАЖЕНИЕ viewport.
Отдельной VL-модели нет — экран анализируешь ТЫ сам (что на странице, куда кликать, координаты).

**КРИТИЧЕСКИЕ ЗАКОНЫ (НАРУШЕНИЕ = ПРОВАЛ):**

**ЗАКОН №1: ИНСТРУМЕНТЫ — ЕДИНСТВЕННАЯ РЕАЛЬНОСТЬ.**
- Запрещено выдумывать URL тендеров, tender_id, содержимое документов или факт скачивания.
- Источник правды — РЕАЛЬНЫЙ результат tool call (JSON с ok/message/url/...) и то, что видно на screenshot.
- В аргументы tools копируй РЕАЛЬНЫЕ значения из предыдущих JSON (href, tender_id, path).
  ЗАПРЕЩЕНЫ плейсхолдеры вроде result['...'] или ${...}.
- finish_platform_task — ТОЛЬКО tool call (не текстом). Вызывай ОДИН раз, когда задача реально завершена.
- success=true только если критерии подтверждены текущим URL / скачанными файлами / processed_tenders.
- Запрещён ранний finish на главной/лендинге, если задача — поиск, карточка или документы.
- Запрещён success=true про «открыта карточка», если текущий URL всё ещё выдача/поиск
  (после click_xy/navigate URL в последнем tool result должен быть карточкой).
- Имея href карточки — предпочитай navigate(href), а не click_xy. click_xy без смены URL = промах, не успех.

**ЗАКОН №2: АЛГОРИТМ МОНИТОРИНГА.**
1. Убедись, что открыта целевая платформа (navigate при необходимости на platform_url).
2. Поиск по keywords — через UI площадки (не хардкодь чужие URL-шаблоны):
   a) Найди поле поиска: screenshot → type_text ОБЯЗАТЕЛЬНО с x,y внутри viewport → submit/Enter.
      type_text без x,y ЗАПРЕЩЁН.
   b) Если координаты плохие — eval_js: найти видимое поле поиска, заполнить keywords, отправить форму.
      Для ссылок используй широкие селекторы (a[href] с notice/regNumber/purchase/tender), не хрупкий CSS одной площадки.
   c) Успех поиска — только после screenshot: на кадре видна выдача/список по запросу,
      не главная и не пустая форма. Иначе НЕ finish(success=true); повтори поиск.
3. Кандидаты карточек:
   - Собери ссылки ТОЛЬКО через eval_js/get_page_text (реальные href из DOM, сверху вниз).
     Не сочиняй URL карточки из номера на скрине без href в tool result.
   - Открывай: navigate(href) → screenshot. Успех открытия = URL в ответе navigate/screenshot
     стал карточкой (не выдача). Если URL не сменился — повтори navigate или другой кандидат.
   - Открывай только карточки этой же площадки; пропускай javascript:, mailto:, служебные
     отчёты/статистику/футер и явные заглушки.
   - Если после navigate 404 / нет документов / list_download_links=0 — НЕ finish; следующий кандидат.
4. Для каждого кандидата:
   a) extract_tender_id(url) — создаёт папку session/<tender_id>/
   b) check_tender_seen — если seen, ПРОПУСТИ
   c) если new и лимит не исчерпан:
      - navigate на карточку (common-info) → screenshot
      - save_tender_overview — титульный overview.md/json (цена, объект, заказчик, ссылка)
      - открой раздел документов → list_download_links → download_url ТОЛЬКО kind=file
        (файлы автоматически в папку этого tender_id)
      - не качай навигацию, футер и служебные ссылки
      - если files пусто — screenshot или click_xy(expect_download=true); иначе следующий тендер
      - mark_tender_seen после обработки
5. Верификация экраном (screenshot обязателен на чекпоинтах):
   - После: открытие площадки, поиск, переход на карточку, открытие документов, сомнительный клик.
   - Смотри пришедшее изображение; при расхождении с ожиданием — исправь шаг, не иди «вслепую».
   - Координаты для type_text/click_xy — только из свежего screenshot (x < width, y < height).
   - eval_js/get_page_text дополняют зрение, но не заменяют проверку ключевых переходов.
6. Не качай десятки одинаковых редакций — последняя версия + уникальные протоколы/решения.
7. login/captcha/403 — finish_platform_task(success=false).
8. finish_platform_task(success=true) только после реальной обработки лимита новых ИЛИ исчерпания валидных кандидатов
   (краткий summary: URL, tender_id, файлы — только из tool results, без плейсхолдеров).
   Перед финальным success=true — screenshot, если ещё не делал на последнем состоянии.

**ЗАКОН №3: ЗАВЕРШЕНИЕ.**
- Итог только через finish_platform_task.
- В summary: сколько новых, какие URL, что скачано / почему не удалось.

**ДОСТУПНЫЕ ИНСТРУМЕНТЫ:**
navigate, screenshot, click_xy, type_text, press_key, scroll, wait,
get_page_text, list_download_links, download_url, eval_js,
extract_tender_id, check_tender_seen, save_tender_overview, mark_tender_seen,
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
) -> str:
    """
    Один multimodal ChatOpenAI + tools.
    screenshot не вызывает отдельный VL: картинка инжектится в messages.
    """
    llm = _build_llm().bind_tools(tools)
    tools_by_name = {t.name: t for t in tools}

    messages: list[Any] = [
        SystemMessage(content=SYSTEM_PROMPT),
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

        if config.AGENT_DEBUG_LOGS and last_text:
            logger.info("LLM step=%s text=%s", step_i, last_text[:500])

        tool_calls = getattr(ai, "tool_calls", None) or []
        if not tool_calls:
            logger.info("multimodal loop: no tool_calls at step=%s", step_i)
            break

        names = [tc.get("name") for tc in tool_calls]
        logger.info("LLM step=%s tool_calls=%s", step_i, names)

        saw_screenshot = False
        for tc in tool_calls:
            name = tc.get("name") or ""
            args = tc.get("args") or {}
            tc_id = tc.get("id") or f"call_{step_i}_{name}"
            tool = tools_by_name.get(name)
            if tool is None:
                observation = f'{{"ok": false, "message": "unknown tool: {name}"}}'
            else:
                if config.AGENT_DEBUG_LOGS:
                    logger.info("tool_call step=%s name=%s args=%s", step_i, name, str(args)[:300])
                observation = await _ainvoke_tool(tool, args if isinstance(args, dict) else {})
            messages.append(ToolMessage(content=observation, tool_call_id=tc_id))
            if name == "screenshot":
                saw_screenshot = True
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

        if ctx.done:
            break

    return ctx.final_summary or last_text


async def run_platform_task(
    platform_url: str,
    keywords: str,
    max_new_tenders: int | None = None,
    max_steps: int | None = None,
    download_subdir: str | None = None,
    instruction: str | None = None,
) -> dict[str, Any]:
    """
    Multimodal tool-calling loop:
    - AGENT_LLM_MODEL вызывает tools и сам смотрит screenshot (image в messages)
    - отдельный AGENT_VL_MODEL / analyze_screenshot не используется
    - SQLite dedup
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

    agent_log_path = setup_agent_file_logging(run_tag=session_name)

    user_input = (instruction or "").strip() or (
        f"Перейди на платформу и найди НОВЫЕ тендеры по ключевым словам, "
        f"скачай документацию. Лимит новых: {max_new}. "
        f"Для каждого нового: extract_tender_id → save_tender_overview на карточке → "
        f"скачай файлы в папку этого тендера → mark_tender_seen. "
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
            downloads_root=downloads,
        )
        ctx.trace.append({"tool": "navigate", "args": {"url": platform_url}, "result": nav})

        tools = build_langchain_tools(ctx)

        logger.info(
            "platform multimodal loop start platform=%s keywords=%s max_iter=%s model=%s",
            ctx.platform,
            keywords,
            max_steps,
            config.AGENT_LLM_MODEL,
        )

        output_text = await run_multimodal_tool_loop(
            ctx,
            tools,
            user_input=user_input,
            platform_url=platform_url,
            keywords=keywords,
            max_new=max_new,
            max_steps=max_steps,
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
        "agent_log_path": str(agent_log_path or current_agent_log_path() or ""),
        "max_steps": max_steps,
    }
