"""Журнал tools-server.

Один файл сервиса: DATA_ROOT/_logs/tools.log. Те же строки идут в stdout,
который оболочка уже складывает в tools-out.log. В каждой строке метка [tools].
"""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from typing import Any

_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|token|bearer)\b\s*[:=]\s*(?:bearer\s+)?\S+"
)
_CALL: ContextVar[dict[str, Any] | None] = ContextVar("tools_call", default=None)
_SKIP = {"/health", "/openapi.json", "/docs", "/redoc", "/favicon.ico"}
_HINTS = ("path", "pages", "parts", "page", "sheet", "mode", "query", "file_type", "job_id")
_SCALARS = (
    "path", "filename", "page_unit", "mode", "match", "matched", "query",
    "content_complete", "requested_scope_complete", "truncated", "visual_attempts",
    "extension", "chars", "returned_files", "total_supported_files", "state",
    "rows_total", "rows_filled", "rows_done", "cells_written", "job_id", "page",
    "frame", "cached", "model", "total_pages", "source",
)


class _Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = _SECRET.sub(r"\1=***", message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def get_logger(name: str = "tools") -> logging.Logger:
    return logging.getLogger(name)


def setup_logging() -> None:
    """Повесить файл и stdout на журналы приложения. Чужие библиотеки туда не пишут."""
    from .config import DATA_ROOT

    for name, logger in list(logging.root.manager.loggerDict.items()):
        if isinstance(logger, logging.Logger) and (name == "tools" or name.startswith(("tools.", "app."))):
            logger.disabled = False
    closed: list[logging.Handler] = []
    for name in ("app", "tools"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.disabled = False
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            if getattr(handler, "_tools", False) and handler not in closed:
                closed.append(handler)
    for handler in closed:
        handler.close()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [tools] %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handlers: list[logging.Handler] = []
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    stream.addFilter(_Redact())
    stream._tools = True  # type: ignore[attr-defined]
    handlers.append(stream)

    path = DATA_ROOT / "_logs" / "tools.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    except OSError as exc:
        for logger_name in ("app", "tools"):
            logging.getLogger(logger_name).addHandler(stream)
        get_logger().warning("tools log file unavailable: %s", exc)
        return
    file.setFormatter(formatter)
    file.addFilter(_Redact())
    file._tools = True  # type: ignore[attr-defined]
    handlers.append(file)
    for logger_name in ("app", "tools"):
        logger = logging.getLogger(logger_name)
        for handler in handlers:
            logger.addHandler(handler)


def begin_call() -> None:
    _CALL.set({})


def record(**fields: Any) -> None:
    current = _CALL.get()
    if current is None:
        return
    for key, value in fields.items():
        if value is not None and value != "":
            current[key] = value


def call_fields() -> dict[str, Any]:
    return dict(_CALL.get() or {})


def hint(args: tuple, kwargs: dict) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key in _HINTS:
        value = kwargs.get(key)
        if isinstance(value, (str, int)) and value != "":
            found[key] = value if isinstance(value, int) else str(value)[:180]
    if args and isinstance(args[0], str) and "path" not in found and "\n" not in args[0][:80]:
        found["path"] = args[0][:180]
    return found


def summarize(result: Any) -> dict[str, Any]:
    if isinstance(result, list):
        return {"items": len(result)}
    if not isinstance(result, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _SCALARS:
        value = result.get(key)
        if isinstance(value, bool) or (isinstance(value, (int, float)) and not isinstance(value, bool)):
            out[key] = value
        elif isinstance(value, str) and value:
            out[key] = value[:180]
    read = result.get("pages_read") or result.get("parts_read")
    if isinstance(read, list) and read:
        out["read"] = _span(read)
    review = result.get("pages_needing_review") or result.get("parts_needing_review")
    if isinstance(review, list) and review:
        out["review"] = _span(review)
    hits = result.get("results")
    if isinstance(hits, list):
        out["hits"] = len(hits)
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        out["errors"] = len(errors)
    codes = _warning_codes(result)
    if codes:
        out["warnings"] = ",".join(codes[:8])
    if result.get("next_cursor"):
        out["continued"] = True
    text = result.get("text") or result.get("content")
    if isinstance(text, str) and "chars" not in out:
        out["chars"] = len(text)
    return out


def format_fields(fields: dict[str, Any]) -> str:
    parts = []
    for key, value in fields.items():
        text = str(value).replace("\n", " ")
        if any(ch.isspace() for ch in text):
            text = '"' + text.replace('"', "'") + '"'
        parts.append(f"{key}={text}")
    return " ".join(parts)


def skip_path(path: str) -> bool:
    return path in _SKIP


def _span(nums: list) -> str:
    values = [n for n in nums if isinstance(n, int)]
    if not values:
        return str(len(nums))
    if len(values) > 12:
        return f"{values[0]}-{values[-1]}:{len(values)}"
    return ",".join(str(n) for n in values)


def _warning_codes(result: dict) -> list[str]:
    found: list[str] = []

    def take(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, str):
                continue
            head = item.split(":", 1)[0].strip()
            if head and " " not in head and head not in found:
                found.append(head)

    take(result.get("warnings"))
    for key in ("pages", "parts"):
        pages = result.get(key)
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict):
                    take(page.get("warnings"))
    return found
