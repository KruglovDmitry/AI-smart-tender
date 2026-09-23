"""Auto-collect vision grounding samples for later backend comparison."""

from __future__ import annotations

import base64
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ... import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_counters: dict[str, int] = {}


def _samples_dir() -> Path:
    return Path(
        getattr(config, "VISION_SAMPLES_DIR", None)
        or (config.DATA_ROOT / "vision_samples")
    ).resolve()


def _enabled() -> bool:
    return bool(getattr(config, "VISION_SAMPLES_ENABLED", True))


def _max_per_run() -> int:
    return int(getattr(config, "VISION_SAMPLES_MAX_PER_RUN", 200) or 200)


def next_step(run_id: str) -> int | None:
    """Allocate next sample index for run_id, or None if capped / disabled."""
    if not _enabled():
        return None
    with _lock:
        n = _counters.get(run_id, 0) + 1
        if n > _max_per_run():
            return None
        _counters[run_id] = n
        return n


def write_sample(
    *,
    run_id: str,
    step: int,
    image_b64: str,
    meta: dict[str, Any],
) -> Path | None:
    """
    Write <run_id>/<NNN>.png + .json. Never raises — logs warning on failure.
    """
    if not _enabled():
        return None
    try:
        root = _samples_dir() / run_id
        root.mkdir(parents=True, exist_ok=True)
        stem = f"{int(step):03d}"
        png_path = root / f"{stem}.png"
        json_path = root / f"{stem}.json"
        if image_b64:
            png_path.write_bytes(base64.b64decode(image_b64, validate=False))
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "step": int(step),
            "label": None,
            **meta,
        }
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return json_path
    except Exception as e:
        logger.warning("vision sample write failed: %s", e)
        return None


def reset_counters_for_tests() -> None:
    with _lock:
        _counters.clear()
