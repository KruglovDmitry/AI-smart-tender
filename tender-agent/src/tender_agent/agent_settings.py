"""Настройки моделей, которые меняются из дашборда (вкладка «Агент»), без перезапуска.

Файл ``<data_dir>/_agent/settings.json`` — общее место для tender-agent и tools-server.
Оболочка один раз переносит в него ключи из ``.env``. Пока файла нет, секрет можно взять
из окружения. Когда файл уже есть, секрет берётся только из него: пустое поле не
подменяется старым ``.env``. Наружу ключи отдаются только маской.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


@dataclass(frozen=True)
class Option:
    key: str  # поле Settings
    label: str
    group: str  # dom | vision | browser | limits
    kind: str = "str"  # str | secret | bool | int
    env: tuple[str, ...] = ()
    default: object = ""


OPTIONS: tuple[Option, ...] = (
    Option("llm_base_url", "Адрес API", "dom", env=("TENDER_AGENT_LLM_BASE_URL", "AGENT_LLM_BASE_URL")),
    Option("llm_api_key", "Ключ", "dom", "secret", ("TENDER_AGENT_LLM_API_KEY", "AGENT_LLM_API_KEY")),
    Option("llm_model", "Модель", "dom", env=("TENDER_AGENT_LLM_MODEL", "AGENT_LLM_MODEL"), default="deepseek-flash"),
    Option("vision_enabled", "Зрение включено", "vision", "bool", ("TENDER_AGENT_VISION_ENABLED",), False),
    Option("vision_max_calls", "Вызовов зрения на выдаче", "vision", "int", ("TENDER_AGENT_VISION_MAX_CALLS",), 2),
    Option("vision_planner_base_url", "VL-планировщик: адрес API", "vision", env=("AV_PLANNER_BASE_URL",),
           default="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    Option("vision_planner_api_key", "VL-планировщик: ключ", "vision", "secret", ("AV_PLANNER_API_KEY", "QWEN_API_KEY")),
    Option("vision_planner_model", "VL-планировщик: модель", "vision", env=("AV_PLANNER_MODEL",), default="qwen3-vl-plus"),
    Option("vision_tars_base_url", "UI-TARS: адрес API", "vision", env=("AV_TARS_BASE_URL",),
           default="http://10.127.0.41:8000/v1"),
    Option("vision_tars_model", "UI-TARS: модель", "vision", env=("AV_TARS_MODEL",), default="ui-tars"),
    Option("vision_tars_api_key", "UI-TARS: ключ", "vision", "secret", ("AV_TARS_API_KEY",), "EMPTY"),
    Option("vision_fallback_model", "Запасная модель координат, если UI-TARS недоступен (на ключе планировщика; «-» — выключить)",
           "vision", env=("AV_GROUND_FALLBACK_MODEL",), default="qwen3-vl-plus"),
    Option("headless", "Скрывать окно браузера", "browser", "bool", ("TENDER_AGENT_HEADLESS",), True),
    Option("downloads_enabled", "Скачивание разрешено", "browser", "bool", ("TENDER_AGENT_DOWNLOADS",), True),
    Option("timeout_seconds", "Время на задание, с", "limits", "int", ("TENDER_AGENT_TIMEOUT_SECONDS",), 300),
    Option("card_timeout_seconds", "Время на карточку, с", "limits", "int", ("TENDER_AGENT_CARD_TIMEOUT_SECONDS",), 90),
)
BY_KEY = {option.key: option for option in OPTIONS}


def settings_path(data_dir: Path) -> Path:
    return data_dir / "_agent" / "settings.json"


def load_overrides(data_dir: Path) -> dict:
    path = settings_path(data_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: value for key, value in data.items() if key in BY_KEY} if isinstance(data, dict) else {}


def _coerce(option: Option, value: object) -> object:
    if option.kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if option.kind == "int":
        return int(value)  # type: ignore[arg-type]
    return str(value).strip()


def _read(data_dir: Path) -> dict:
    try:
        data = json.loads(settings_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _locked(data_dir: Path) -> bool:
    """Файл дашборда уже создан: секреты из .env больше не подставляются."""
    return settings_path(data_dir).is_file()


def save_overrides(data_dir: Path, changes: dict) -> dict:
    """Записать изменения: значение — задать, None или "" — убрать из файла."""
    current = _read(data_dir)
    for key, value in changes.items():
        option = BY_KEY.get(key)
        if option is None:
            raise ValueError(f"Неизвестная настройка: {key}")
        if value is None or (isinstance(value, str) and not value.strip()):
            current.pop(key, None)
        else:
            current[key] = _coerce(option, value)
            if option.kind == "secret":
                current.pop("keys_cleared", None)
    path = settings_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return load_overrides(data_dir)


def apply_overrides(settings: Settings) -> Settings:
    """Settings задания с учётом файла из дашборда."""
    overrides = load_overrides(settings.data_dir)
    locked = _locked(settings.data_dir)
    if not overrides and not locked:
        return settings
    values = {}
    if locked:
        for option in OPTIONS:
            if option.kind == "secret" and option.key not in overrides:
                values[option.key] = ""
    for key, value in overrides.items():
        try:
            values[key] = _coerce(BY_KEY[key], value)
        except (TypeError, ValueError):
            continue
    if "llm_base_url" in values:
        values["llm_base_url"] = str(values["llm_base_url"]).rstrip("/")
    return dataclasses.replace(settings, **values)


def _env_value(option: Option) -> str | None:
    for name in option.env:
        raw = os.environ.get(name)
        if raw not in (None, ""):
            return raw
    return None


def mask(value: str) -> str:
    if not value:
        return ""
    return "••••" + value[-4:] if len(value) > 6 else "••••"


def describe(data_dir: Path) -> list[dict]:
    """Настройки для интерфейса: действующее значение, откуда оно и что задано в дашборде. Ключи — маской."""
    overrides = load_overrides(data_dir)
    rows = []
    for option in OPTIONS:
        if option.key in overrides:
            value, source = overrides[option.key], "dashboard"
        elif option.kind == "secret" and _locked(data_dir):
            value, source = "", "default"
        elif (env := _env_value(option)) is not None:
            value, source = env, "env"
        else:
            value, source = option.default, "default"
        if option.kind == "bool":
            value = _coerce(option, value)
        elif option.kind == "int":
            try:
                value = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                value = option.default
        shown = mask(str(value)) if option.kind == "secret" else value
        rows.append({"key": option.key, "label": option.label, "group": option.group, "kind": option.kind,
                     "value": shown, "source": source, "set": bool(str(value))})
    return rows


def effective(data_dir: Path, key: str) -> str:
    """Действующее значение (без маски) — для проверки подключения."""
    overrides = load_overrides(data_dir)
    option = BY_KEY[key]
    if key in overrides:
        return str(overrides[key])
    if option.kind == "secret" and _locked(data_dir):
        return ""
    env = _env_value(option)
    return env if env is not None else str(option.default)
