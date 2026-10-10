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
    max_llm_calls: int = 12
    max_retries: int = 2
    timeout_seconds: int = 120
    max_pages: int = 5
    max_downloads: int = 20
    max_download_bytes: int = 20_000_000
    headless: bool = True
    allowed_hosts: tuple[str, ...] = ()
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    data_dir: Path = _REPO_ROOT / "data" / "tenders"

    @classmethod
    def from_env(cls) -> Settings:
        _load_project_env()
        data_dir = _data_dir()
        return cls(
            default_limit=int(os.getenv("TENDER_AGENT_DEFAULT_LIMIT", "5")),
            max_actions=int(os.getenv("TENDER_AGENT_MAX_ACTIONS", "40")),
            max_llm_calls=int(os.getenv("TENDER_AGENT_MAX_LLM_CALLS", "12")),
            max_retries=int(os.getenv("TENDER_AGENT_MAX_RETRIES", "2")),
            timeout_seconds=int(os.getenv("TENDER_AGENT_TIMEOUT_SECONDS", "120")),
            max_pages=int(os.getenv("TENDER_AGENT_MAX_PAGES", "5")),
            max_downloads=int(os.getenv("TENDER_AGENT_MAX_DOWNLOADS", "20")),
            max_download_bytes=int(os.getenv("TENDER_AGENT_MAX_DOWNLOAD_BYTES", "20000000")),
            headless=_flag("TENDER_AGENT_HEADLESS", True),
            allowed_hosts=_hosts(os.getenv("TENDER_AGENT_ALLOWED_HOSTS", "")),
            llm_base_url=os.getenv("TENDER_AGENT_LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.getenv("TENDER_AGENT_LLM_API_KEY", ""),
            llm_model=os.getenv("TENDER_AGENT_LLM_MODEL", ""),
            data_dir=data_dir,
        )
