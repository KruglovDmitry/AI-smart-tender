from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _hosts(raw: str) -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


# src/tender_agent/config.py → каталог пакета tender_agent → корень репозитория
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _PACKAGE_ROOT.parent


def _load_project_env() -> None:
    path = _PACKAGE_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _data_dir() -> Path:
    raw = os.getenv("TENDER_AGENT_DATA_DIR", "data/tenders")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    default_limit: int = 5
    max_actions: int = 40
    # Модель решает каждый шаг, поэтому бюджет времени больше, чем был у эвристик.
    timeout_seconds: int = 300
    max_pages: int = 5
    # Карточка закупки: свой бюджет времени (включая зрение) и шагов цикла.
    card_timeout_seconds: int = 90
    card_max_steps: int = 10
    # Общий выключатель скачивания: false — файлы не качаются, даже если задание просит.
    downloads_enabled: bool = True
    # Файлов на одну закупку.
    max_downloads: int = 30
    max_download_bytes: int = 20_000_000
    headless: bool = True
    allowed_hosts: tuple[str, ...] = ()
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    # Запасной шаг по скриншотам (библиотека ai_vision), см. agent/vision.py.
    vision_enabled: bool = False
    vision_max_calls: int = 2
    vision_max_steps: int = 10
    # Модели зрения (ai_vision): пусто — ai_vision берёт свои AV_* из окружения.
    # Задаются из дашборда (вкладка «Агент», agent_settings.py).
    vision_planner_base_url: str = ""
    vision_planner_api_key: str = ""
    vision_planner_model: str = ""
    vision_tars_base_url: str = ""
    vision_tars_api_key: str = ""
    vision_tars_model: str = ""
    vision_fallback_model: str = ""  # запасная модель координат на ключе планировщика; «-» — выключить
    # Трейс прогона: data_dir/_agent/runs/… (см. observability/trace.py).
    trace_enabled: bool = True
    trace_screenshots: str = "errors"
    # База найденных закупок в data_dir/_agent/agent.sqlite3 (storage/tenders.py).
    store_enabled: bool = True
    # SQLAlchemy URL базы закупок; пусто — SQLite в data_dir/_agent/agent.sqlite3.
    db_url: str = ""
    data_dir: Path = _REPO_ROOT / "data" / "tenders"

    @classmethod
    def from_env(cls) -> Settings:
        _load_project_env()
        data_dir = _data_dir()
        return cls(
            default_limit=int(os.getenv("TENDER_AGENT_DEFAULT_LIMIT", "5")),
            max_actions=int(os.getenv("TENDER_AGENT_MAX_ACTIONS", "40")),
            timeout_seconds=int(os.getenv("TENDER_AGENT_TIMEOUT_SECONDS", "300")),
            max_pages=int(os.getenv("TENDER_AGENT_MAX_PAGES", "5")),
            card_timeout_seconds=int(os.getenv("TENDER_AGENT_CARD_TIMEOUT_SECONDS", "90")),
            card_max_steps=int(os.getenv("TENDER_AGENT_CARD_MAX_STEPS", "10")),
            downloads_enabled=_flag("TENDER_AGENT_DOWNLOADS", True),
            max_downloads=int(os.getenv("TENDER_AGENT_MAX_DOWNLOADS", "30")),
            max_download_bytes=int(os.getenv("TENDER_AGENT_MAX_DOWNLOAD_BYTES", "20000000")),
            headless=_flag("TENDER_AGENT_HEADLESS", True),
            allowed_hosts=_hosts(os.getenv("TENDER_AGENT_ALLOWED_HOSTS", "")),
            llm_base_url=os.getenv("TENDER_AGENT_LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.getenv("TENDER_AGENT_LLM_API_KEY", ""),
            llm_model=os.getenv("TENDER_AGENT_LLM_MODEL", ""),
            vision_enabled=_flag("TENDER_AGENT_VISION_ENABLED", False),
            vision_max_calls=int(os.getenv("TENDER_AGENT_VISION_MAX_CALLS", "2")),
            vision_max_steps=int(os.getenv("TENDER_AGENT_VISION_MAX_STEPS", "10")),
            trace_enabled=_flag("TENDER_AGENT_TRACE", True),
            trace_screenshots=os.getenv("TENDER_AGENT_TRACE_SCREENSHOTS", "errors"),
            store_enabled=_flag("TENDER_AGENT_STORE", True),
            db_url=os.getenv("TENDER_AGENT_DB_URL", ""),
            data_dir=data_dir,
        )
