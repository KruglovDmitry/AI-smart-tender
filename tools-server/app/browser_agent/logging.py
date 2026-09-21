"""Agent file logging — console + per-run log under data/_logs/agent/."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from pathlib import Path

from .. import config

# Avoid clashing with this module's name (`app.browser_agent.logging`).
_logging = importlib.import_module("logging")

_FILE_HANDLER: _logging.Handler | None = None
_CURRENT_LOG_PATH: Path | None = None


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


def log_tool_step(name: str, args: dict, result: object) -> None:
    if not config.AGENT_DEBUG_LOGS:
        return
    logger = _logging.getLogger("app.browser_agent.tools")
    args_s = str(args)[:800]
    if isinstance(result, dict):
        slim = {k: v for k, v in result.items() if k not in {"vl", "image_b64", "text"}}
        if "text" in result:
            slim["text"] = str(result.get("text") or "")[:200]
        if "value" in result:
            slim["value"] = str(result.get("value"))[:400]
        res_s = str(slim)[:1200]
    else:
        res_s = str(result)[:1200]
    logger.info("TOOL %s args=%s result=%s", name, args_s, res_s)
