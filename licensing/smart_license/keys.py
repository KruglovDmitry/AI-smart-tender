"""Секреты моделей лежат в файле дашборда. При истёкшей лицензии они стираются."""

from __future__ import annotations

import json
import os
from pathlib import Path

SECRET_FIELDS = (
    "llm_api_key",
    "vision_planner_api_key",
    "vision_tars_api_key",
    "mail_password",
    "search_api_key",
)


def settings_path() -> Path | None:
    agent = os.environ.get("TENDER_AGENT_DATA_DIR")
    if agent:
        return Path(agent) / "_agent" / "settings.json"
    root = os.environ.get("DATA_ROOT")
    if root:
        return Path(root) / "tenders" / "_agent" / "settings.json"
    return None


def clear_model_keys() -> None:
    path = settings_path()
    if path is None or not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    if data.get("keys_cleared") and not any(data.get(name) for name in SECRET_FIELDS):
        return
    for name in SECRET_FIELDS:
        data.pop(name, None)
    data["keys_cleared"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
