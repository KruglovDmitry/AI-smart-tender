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

# Browser agent (internal tool-calling loop)
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
# Verbose agent step logs (LLM text, tool args/results) → console + data/_logs/agent/
AGENT_DEBUG_LOGS = os.getenv("AGENT_DEBUG_LOGS", "true").lower() in {
    "1",
    "true",
    "yes",
}
AGENT_LOG_DIR = Path(
    os.getenv("AGENT_LOG_DIR", str(DATA_ROOT / "_logs" / "agent"))
).resolve()

# OpenAI-compatible chat API for the browser agent
# AGENT_LLM_MODEL — multimodal tool-calling brain (also sees screenshots)
# AGENT_VL_ENABLED — attach screenshot images into the same model context (no separate VL call)
# AGENT_VL_MODEL — legacy/unused when vl_mode=inline_multimodal (kept for env compat)
AGENT_LLM_BASE_URL = os.getenv("AGENT_LLM_BASE_URL", "").rstrip("/")
AGENT_LLM_API_KEY = os.getenv("AGENT_LLM_API_KEY", "")
AGENT_LLM_MODEL = os.getenv("AGENT_LLM_MODEL", "qwen3.7-plus")
AGENT_VL_MODEL = os.getenv("AGENT_VL_MODEL", "qwen3.7-plus")
AGENT_VL_ENABLED = os.getenv("AGENT_VL_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
}

# Platform monitoring agent (LangChain + SQLite dedup)
PLATFORM_MAX_STEPS = int(os.getenv("PLATFORM_MAX_STEPS", "90"))
PLATFORM_MAX_NEW_TENDERS = int(os.getenv("PLATFORM_MAX_NEW_TENDERS", "3"))
# Max document files to download per tender (priority docs; skip junk/wrappers)
PLATFORM_MAX_FILES_PER_TENDER = int(os.getenv("PLATFORM_MAX_FILES_PER_TENDER", "5"))
# full = domain helpers (collect/filter/overview/…); browser = only low-level browser tools
PLATFORM_AGENT_MODE = os.getenv("PLATFORM_AGENT_MODE", "full").strip().lower()
SEEN_TENDERS_DB = Path(
    os.getenv("SEEN_TENDERS_DB", str(DATA_ROOT / "_state" / "seen_tenders.sqlite3"))
).resolve()

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
