from __future__ import annotations

import os
from pathlib import Path

# tools-server/app/config.py → repo root (AI-smart-tender)
_REPO_ROOT = Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv

    # Local debug: load repo .env without overriding already-set env (Docker/compose).
    load_dotenv(_REPO_ROOT / ".env", override=False)
except ImportError:
    pass

DATA_ROOT = Path(os.getenv("DATA_ROOT", str(_REPO_ROOT / "data"))).resolve()
HOST = os.getenv("TOOLS_HOST", "0.0.0.0")
PORT = int(os.getenv("TOOLS_PORT", "8000"))

DEFAULT_MAX_CHARS = int(os.getenv("DEFAULT_MAX_CHARS", "120000"))
DEFAULT_MAX_FILES = int(os.getenv("DEFAULT_MAX_FILES", "30"))
FETCH_TIMEOUT_SEC = float(os.getenv("FETCH_TIMEOUT_SEC", "30"))

# Playwright browser harness (used by adapters / platform agent)
BROWSER_HEADLESS = os.getenv("BROWSER_HEADLESS", "true").lower() in {"1", "true", "yes"}
BROWSER_VIEWPORT_WIDTH = int(os.getenv("BROWSER_VIEWPORT_WIDTH", "1280"))
BROWSER_VIEWPORT_HEIGHT = int(os.getenv("BROWSER_VIEWPORT_HEIGHT", "900"))
BROWSER_MAX_STEPS = int(os.getenv("BROWSER_MAX_STEPS", "20"))
BROWSER_NAV_TIMEOUT_MS = int(os.getenv("BROWSER_NAV_TIMEOUT_MS", "60000"))
BROWSER_DOWNLOADS_DIR = Path(
    os.getenv("BROWSER_DOWNLOADS_DIR", str(DATA_ROOT / "tenders" / "_browser"))
).resolve()
BROWSER_PROFILE_DIR = Path(
    os.getenv(
        "BROWSER_PROFILE_DIR",
        str(_REPO_ROOT / ".browser-profile"),
    )
).resolve()
AGENT_DEBUG_LOGS = os.getenv("AGENT_DEBUG_LOGS", "true").lower() in {
    "1",
    "true",
    "yes",
}
AGENT_LOG_DIR = Path(
    os.getenv("AGENT_LOG_DIR", str(DATA_ROOT / "_logs" / "agent"))
).resolve()

# Primary LLM (DeepSeek text tool-caller)
AGENT_LLM_BASE_URL = os.getenv("AGENT_LLM_BASE_URL", "").rstrip("/")
AGENT_LLM_API_KEY = os.getenv("AGENT_LLM_API_KEY", "")
AGENT_LLM_MODEL = os.getenv("AGENT_LLM_MODEL", "deepseek-flash")
AGENT_PRIMARY_MODEL = os.getenv("AGENT_PRIMARY_MODEL", "") or AGENT_LLM_MODEL

# Platform monitoring agent (LangChain + SQLite dedup)
PLATFORM_MAX_STEPS = int(os.getenv("PLATFORM_MAX_STEPS", "90"))
PLATFORM_MAX_NEW_TENDERS = int(os.getenv("PLATFORM_MAX_NEW_TENDERS", "3"))
PLATFORM_MAX_FILES_PER_TENDER = int(os.getenv("PLATFORM_MAX_FILES_PER_TENDER", "10"))
PLATFORM_AGENT_MODE = os.getenv("PLATFORM_AGENT_MODE", "platform").strip().lower()
SEEN_TENDERS_DB = Path(
    os.getenv("SEEN_TENDERS_DB", str(DATA_ROOT / "_state" / "seen_tenders.sqlite3"))
).resolve()

# EIS test: disable common-info→documents URL helper so agent must open the tab via DOM
EIS_TEST_NO_DOCS_ROUTE = os.getenv("EIS_TEST_NO_DOCS_ROUTE", "0").lower() in {
    "1",
    "true",
    "yes",
}

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".rtf",
    ".zip",
}
