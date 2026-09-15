"""Internal browser agent loop: tool-calling brain + optional VL for screenshots."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .. import config
from .schemas import BROWSER_TOOL_SCHEMAS, SYSTEM_PROMPT
from .session import browser_runtime
from .tools import TOOL_HANDLERS
from .vision import analyze_screenshot, format_vl_for_agent

logger = logging.getLogger(__name__)


def _require_llm() -> None:
    if not config.AGENT_LLM_BASE_URL:
        raise RuntimeError(
            "AGENT_LLM_BASE_URL is not set. "
            "Example: https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )
    if not config.AGENT_LLM_API_KEY:
        raise RuntimeError("AGENT_LLM_API_KEY is not set")


def _chat_url() -> str:
    base = config.AGENT_LLM_BASE_URL
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


async def _llm_chat(messages: list[dict[str, Any]]) -> dict[str, Any]:
    _require_llm()
    headers = {
        "Authorization": f"Bearer {config.AGENT_LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    # Tool model gets text only (VL advice already expanded to text)
    safe_messages: list[dict[str, Any]] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            texts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            safe_messages.append({**m, "content": "\n".join(t for t in texts if t)})
        else:
            safe_messages.append(m)

    payload = {
        "model": config.AGENT_LLM_MODEL,
        "messages": safe_messages,
        "tools": BROWSER_TOOL_SCHEMAS,
        "tool_choice": "auto",
        "temperature": 0.1,
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(_chat_url(), headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM error {resp.status_code}: {resp.text[:800]}")
        return resp.json()


def _rel_data_path(path: str) -> str:
    try:
        p = Path(path).resolve()
        return str(p.relative_to(config.DATA_ROOT)).replace("\\", "/")
    except Exception:
        return path


async def _dispatch(rt, name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return {"ok": False, "action": name, "message": f"Unknown tool: {name}"}
    if name == "navigate":
        return await handler(rt, url=args.get("url", ""))
    if name == "screenshot":
        return await handler(rt)
    if name == "click_xy":
        return await handler(
            rt,
            x=float(args["x"]),
            y=float(args["y"]),
            expect_download=bool(args.get("expect_download", False)),
        )
    if name == "type_text":
        return await handler(
            rt,
            text=str(args.get("text", "")),
            x=args.get("x"),
            y=args.get("y"),
            clear=bool(args.get("clear", True)),
        )
    if name == "press_key":
        return await handler(rt, key=str(args.get("key") or "Enter"))
    if name == "scroll":
        return await handler(
            rt,
            delta_y=int(args.get("delta_y") or 600),
            times=int(args.get("times") or 1),
        )
    if name == "wait":
        return await handler(rt, seconds=float(args.get("seconds") or 1))
    if name == "get_page_text":
        return await handler(rt, max_chars=int(args.get("max_chars") or 12000))
    if name == "list_download_links":
        return await handler(rt, limit=int(args.get("limit") or 40))
    if name == "download_url":
        return await handler(
            rt,
            url=str(args.get("url") or ""),
            suggested_name=args.get("suggested_name"),
        )
    if name == "finish":
        return await handler(
            rt,
            summary=str(args.get("summary") or ""),
            success=bool(args.get("success", True)),
        )
    return await handler(rt, **args)


async def run_browser_task(
    task: str,
    url: str | None = None,
    max_steps: int | None = None,
    download_subdir: str | None = None,
) -> dict[str, Any]:
    """
    Tool-calling agent (AGENT_LLM_MODEL) chooses DOM vs screenshot.
    After screenshot, AGENT_VL_MODEL analyzes the image (AI-tender style)
    and returns text advice — no grounding stage, no tools on VL.
    """
    max_steps = max_steps or config.BROWSER_MAX_STEPS
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("url must be http(s)")

    downloads = config.BROWSER_DOWNLOADS_DIR
    if download_subdir:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in download_subdir)[
            :80
        ]
        downloads = config.DATA_ROOT / "tenders" / safe
    downloads.mkdir(parents=True, exist_ok=True)

    trace: list[dict[str, Any]] = []
    user_task = task.strip()
    if url:
        user_task = f"{user_task}\n\nСтартовый URL: {url}"

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_task},
    ]

    async with browser_runtime(downloads_dir=downloads) as rt:
        if url:
            nav = await _dispatch(rt, "navigate", {"url": url})
            trace.append({"tool": "navigate", "args": {"url": url}, "result": nav})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Browser already navigated to {url}. "
                        f"Result: {json.dumps(nav, ensure_ascii=False)[:1500]}"
                    ),
                }
            )

        final: dict[str, Any] | None = None

        for step in range(max_steps):
            logger.info("browser agent step %s/%s", step + 1, max_steps)
            data = await _llm_chat(messages)
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []

            asst: dict[str, Any] = {
                "role": "assistant",
                "content": message.get("content"),
            }
            if tool_calls:
                asst["tool_calls"] = tool_calls
            messages.append(asst)

            if not tool_calls:
                content = (message.get("content") or "").strip()
                final = {
                    "ok": True,
                    "action": "finish",
                    "success": bool(rt.downloaded_files),
                    "message": content or "Agent stopped without finish()",
                    "downloaded_files": list(rt.downloaded_files),
                    "url": rt.page.url,
                    "done": True,
                }
                trace.append({"tool": "finish", "args": {}, "result": final})
                break

            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = (
                        json.loads(raw_args)
                        if isinstance(raw_args, str)
                        else dict(raw_args)
                    )
                except json.JSONDecodeError:
                    args = {}

                result = await _dispatch(rt, name, args)
                if "file" in result and result["file"]:
                    result["file_rel"] = _rel_data_path(str(result["file"]))
                if "downloaded_files" in result:
                    result["downloaded_files_rel"] = [
                        _rel_data_path(f) for f in result["downloaded_files"]
                    ]

                if (
                    name == "screenshot"
                    and result.get("ok")
                    and rt.last_screenshot_b64
                    and config.AGENT_VL_ENABLED
                ):
                    vl = await analyze_screenshot(
                        image_b64=rt.last_screenshot_b64,
                        width=int(result.get("width") or config.BROWSER_VIEWPORT_WIDTH),
                        height=int(
                            result.get("height") or config.BROWSER_VIEWPORT_HEIGHT
                        ),
                        url=str(result.get("url") or rt.page.url),
                        task=user_task,
                    )
                    result["vl"] = {
                        "ok": vl.get("ok"),
                        "model": vl.get("model"),
                        "advice": vl.get("advice"),
                        "error": vl.get("error"),
                    }
                    trace.append(
                        {
                            "tool": "vl_analyze_screenshot",
                            "args": {"model": config.AGENT_VL_MODEL},
                            "result": result["vl"],
                        }
                    )
                    slim = {k: v for k, v in result.items() if k != "vl"}
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or name,
                            "content": json.dumps(slim, ensure_ascii=False)[:8000],
                        }
                    )
                    messages.append(
                        {"role": "user", "content": format_vl_for_agent(vl)}
                    )
                else:
                    tool_content = json.dumps(result, ensure_ascii=False)
                    if len(tool_content) > 20000:
                        tool_content = tool_content[:20000] + "...(truncated)"
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or name,
                            "content": tool_content,
                        }
                    )

                trace.append({"tool": name, "args": args, "result": result})

                if result.get("done") or name == "finish":
                    final = result
                    break

                # After screenshot+VL, return control to the LLM with VL advice
                # (do not execute sibling tool_calls from the same turn).
                if name == "screenshot" and result.get("vl") is not None:
                    break

            if final is not None:
                break
        else:
            final = {
                "ok": False,
                "action": "finish",
                "success": False,
                "message": f"Stopped: reached max_steps={max_steps}",
                "downloaded_files": list(rt.downloaded_files),
                "url": rt.page.url,
                "done": True,
            }
            trace.append({"tool": "finish", "args": {}, "result": final})

    files = final.get("downloaded_files") or []
    files_rel = [_rel_data_path(f) for f in files]

    return {
        "success": bool(final.get("success", False)),
        "summary": final.get("message") or "",
        "final_url": final.get("url"),
        "downloaded_files": files,
        "downloaded_files_rel": files_rel,
        "steps": len(trace),
        "trace": trace,
        "downloads_dir": str(downloads),
        "model": config.AGENT_LLM_MODEL,
        "vl_model": config.AGENT_VL_MODEL,
        "vl_enabled": config.AGENT_VL_ENABLED,
    }
