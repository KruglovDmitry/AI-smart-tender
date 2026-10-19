from __future__ import annotations

import logging
import re

_SECRET = re.compile(
    r"(?i)(api[_-]?key|authorization|cookie|token)\s*[:=]\s*\S+"
)


class _Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = _SECRET.sub(r"\1=***", message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def get_logger() -> logging.Logger:
    logger = logging.getLogger("tender_agent")
    if not any(isinstance(item, _Redact) for item in logger.filters):
        logger.addFilter(_Redact())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        handler.addFilter(_Redact())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
