"""Agent file logging — console + per-run log under data/_logs/agent/."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config

# Avoid clashing with this module's name (`app.browser_agent.logging`).
_logging = importlib.import_module("logging")

_FILE_HANDLER: _logging.Handler | None = None
_CURRENT_LOG_PATH: Path | None = None

# Keys that blow up logs (base64 / huge blobs) — keep a size hint only.
_BLOB_KEYS = frozenset({"image_b64", "vl", "screenshot_b64", "b64"})


def agent_log_dir() -> Path:
    d = Path(
        getattr(config, "AGENT_LOG_DIR", None)
        or (config.DATA_ROOT / "_logs" / "agent")
    ).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def current_agent_log_path() -> Path | None:
    return _CURRENT_LOG_PATH


def setup_agent_file_logging(run_tag: str | None = None) -> Path:
    """
    Attach a UTF-8 FileHandler to browser_agent / browser_tool loggers.
    One file per run (tag or timestamp). Safe to call multiple times.
    """
    global _FILE_HANDLER, _CURRENT_LOG_PATH

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in (run_tag or "run"))[:60]
    path = agent_log_dir() / f"{safe_tag}-{stamp}.log"
    _CURRENT_LOG_PATH = path

    root_names = (
        "app.browser_agent",
        "app.browser_tool",
    )
    fmt = _logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if _FILE_HANDLER is not None:
        for name in root_names:
            _logging.getLogger(name).removeHandler(_FILE_HANDLER)
        try:
            _FILE_HANDLER.close()
        except Exception:
            pass
        _FILE_HANDLER = None

    fh = _logging.FileHandler(path, encoding="utf-8")
    fh.setLevel(_logging.DEBUG if config.AGENT_DEBUG_LOGS else _logging.INFO)
    fh.setFormatter(fmt)
    _FILE_HANDLER = fh

    for name in root_names:
        lg = _logging.getLogger(name)
        lg.setLevel(_logging.DEBUG if config.AGENT_DEBUG_LOGS else _logging.INFO)
        lg.addHandler(fh)
        lg.propagate = True

    _logging.getLogger("app.browser_agent").info("agent file log started: %s", path)
    latest = agent_log_dir() / "latest.log"
    try:
        latest.write_text(f"{path}\n", encoding="utf-8")
    except Exception:
        pass
    return path


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)


def scrub_for_log(obj: Any) -> Any:
    """Deep-copy-ish scrub: drop base64 blobs, keep everything else intact."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _BLOB_KEYS and isinstance(v, str) and len(v) > 200:
                out[k] = f"[omitted len={len(v)}]"
            else:
                out[k] = scrub_for_log(v)
        return out
    if isinstance(obj, list):
        return [scrub_for_log(x) for x in obj]
    if isinstance(obj, str) and obj.startswith("data:image") and len(obj) > 200:
        return f"[data:image omitted len={len(obj)}]"
    return obj


def log_tool_step(name: str, args: dict, result: object) -> None:
    if not config.AGENT_DEBUG_LOGS:
        return
    logger = _logging.getLogger("app.browser_agent.tools")
    args_s = _safe_json(scrub_for_log(args if isinstance(args, dict) else {"_": args}))
    res_s = _safe_json(scrub_for_log(result))
    logger.info("TOOL %s args=%s result=%s", name, args_s, res_s)


def _content_for_log(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[Any] = []
        for p in content:
            if not isinstance(p, dict):
                parts.append(scrub_for_log(p))
                continue
            ptype = p.get("type")
            if ptype in {"image_url", "image"}:
                url = ""
                if isinstance(p.get("image_url"), dict):
                    url = str(p["image_url"].get("url") or "")
                elif isinstance(p.get("image_url"), str):
                    url = p["image_url"]
                parts.append({"type": ptype, "image_url": f"[omitted len={len(url)}]"})
            else:
                parts.append(scrub_for_log(p))
        return parts
    return scrub_for_log(content)


def serialize_message_for_log(msg: Any) -> dict[str, Any]:
    """LangChain message → JSON-friendly dict without image payloads."""
    cls = type(msg).__name__
    entry: dict[str, Any] = {
        "type": cls,
        "content": _content_for_log(getattr(msg, "content", None)),
    }
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        entry["tool_calls"] = scrub_for_log(tool_calls)
    tc_id = getattr(msg, "tool_call_id", None)
    if tc_id:
        entry["tool_call_id"] = tc_id
    name = getattr(msg, "name", None)
    if name:
        entry["name"] = name
    return entry


def log_messages(step_i: int, messages: list[Any], *, label: str = "messages") -> None:
    """Dump full LangChain message list (images scrubbed) for one step."""
    if not config.AGENT_DEBUG_LOGS:
        return
    logger = _logging.getLogger("app.browser_agent")
    payload = [serialize_message_for_log(m) for m in messages]
    logger.info(
        "LANGCHAIN %s step=%s count=%s\n%s",
        label,
        step_i,
        len(payload),
        _safe_json(payload),
    )
