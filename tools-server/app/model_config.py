"""Ключи моделей из файла дашборда.

Пока файла нет (разработка и тесты), берутся переменные окружения.
Когда файл есть, пустой ключ не подменяется окружением.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def settings_file() -> Path:
    from .config import DATA_ROOT

    return DATA_ROOT / "tenders" / "_agent" / "settings.json"


def _stored() -> dict | None:
    path = settings_file()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _field(data: dict, name: str) -> str:
    return str(data.get(name) or "").strip()


def _env(*names: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def llm_config() -> tuple[str, str, str]:
    data = _stored()
    if data is not None:
        base, key, model = _field(data, "llm_base_url"), _field(data, "llm_api_key"), _field(data, "llm_model")
    else:
        base = _env("TOOLS_LLM_BASE_URL", "AGENT_LLM_BASE_URL", "TENDER_AGENT_LLM_BASE_URL")
        key = _env("TOOLS_LLM_API_KEY", "AGENT_LLM_API_KEY", "TENDER_AGENT_LLM_API_KEY")
        model = _env("TOOLS_LLM_MODEL", "AGENT_LLM_MODEL", "TENDER_AGENT_LLM_MODEL")
    if not (base and key and model):
        raise RuntimeError("Модель не настроена. Укажите адрес, ключ и модель во вкладке «Агент» дашборда.")
    return base.rstrip("/"), key, model


def vl_config() -> tuple[str, str, str]:
    data = _stored()
    if data is not None:
        base = _field(data, "vision_planner_base_url")
        key = _field(data, "vision_planner_api_key")
        model = _field(data, "vision_planner_model")
    else:
        base = _env("TOOLS_VL_BASE_URL", "AV_PLANNER_BASE_URL")
        key = _env("TOOLS_VL_API_KEY", "AV_PLANNER_API_KEY", "QWEN_API_KEY")
        model = _env("TOOLS_VL_MODEL", "AV_PLANNER_MODEL")
    if not base:
        base = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    if not model:
        model = "qwen3-vl-plus"
    if not key:
        raise RuntimeError("Зрение не настроено. Укажите ключ VL во вкладке «Агент» дашборда.")
    return base.rstrip("/"), key, model
