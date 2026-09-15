from __future__ import annotations

import os
from pathlib import Path

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data")).resolve()
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
    os.getenv("BROWSER_PROFILE_DIR", "/tmp/tender-browser-profile")
).resolve()

# OpenAI-compatible chat API for the browser agent
# AGENT_LLM_MODEL — tool-calling brain (qwen-max / qwen-plus)
# AGENT_VL_MODEL  — vision for screenshots (qwen-vl-plus), as in AI-tender
AGENT_LLM_BASE_URL = os.getenv("AGENT_LLM_BASE_URL", "").rstrip("/")
AGENT_LLM_API_KEY = os.getenv("AGENT_LLM_API_KEY", "")
AGENT_LLM_MODEL = os.getenv("AGENT_LLM_MODEL", "qwen-max")
AGENT_VL_MODEL = os.getenv("AGENT_VL_MODEL", "qwen-vl-plus")
AGENT_VL_ENABLED = os.getenv("AGENT_VL_ENABLED", "true").lower() in {
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
