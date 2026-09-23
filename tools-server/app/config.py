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
# AGENT_PRIMARY_MODEL — text tool-caller (preferred); falls back to AGENT_LLM_MODEL
# AGENT_VL_MODEL — vision grounding/inspect model (click_on_screen / inspect_screen)
# AGENT_VISION_BACKEND — registry key (qwen_vl | ui_tars)
# AGENT_PRIMARY_MULTIMODAL — if true AND tools_mode=browser, primary gets inline screenshots
AGENT_LLM_BASE_URL = os.getenv("AGENT_LLM_BASE_URL", "").rstrip("/")
AGENT_LLM_API_KEY = os.getenv("AGENT_LLM_API_KEY", "")
AGENT_LLM_MODEL = os.getenv("AGENT_LLM_MODEL", "qwen3.7-plus")
AGENT_PRIMARY_MODEL = os.getenv("AGENT_PRIMARY_MODEL", "") or AGENT_LLM_MODEL
AGENT_VL_MODEL = os.getenv("AGENT_VL_MODEL", "qwen3-vl-plus")
AGENT_VISION_BACKEND = os.getenv("AGENT_VISION_BACKEND", "ui_tars").strip().lower()
# pixel — model returns PNG/viewport pixels; norm1000 — 0..1000 grid over PNG
_AGENT_VL_COORDS = os.getenv("AGENT_VL_COORDS", "pixel").strip().lower()
AGENT_VL_COORDS = _AGENT_VL_COORDS if _AGENT_VL_COORDS in {"pixel", "norm1000"} else "pixel"
# UI-TARS-1.5 via OpenAI-compatible vLLM (Mode A GROUNDING fallback).
# Leave empty in defaults — set UI_TARS_BASE_URL in .env (e.g. http://host:8000).
UI_TARS_BASE_URL = os.getenv("UI_TARS_BASE_URL", "").rstrip("/")
UI_TARS_MODEL = os.getenv("UI_TARS_MODEL", "ui-tars")
UI_TARS_API_KEY = os.getenv("UI_TARS_API_KEY", "EMPTY")
AGENT_PRIMARY_MULTIMODAL = os.getenv("AGENT_PRIMARY_MULTIMODAL", "false").lower() in {
    "1",
    "true",
    "yes",
}
# Inline screenshot injection into primary context (browser ablation only).
_vl_env = os.getenv("AGENT_VL_ENABLED")
if _vl_env is None:
    AGENT_VL_ENABLED = AGENT_PRIMARY_MULTIMODAL
else:
    AGENT_VL_ENABLED = _vl_env.lower() in {"1", "true", "yes"}
if AGENT_PRIMARY_MULTIMODAL:
    AGENT_VL_ENABLED = True

VISION_SAMPLES_ENABLED = os.getenv("VISION_SAMPLES_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
}
VISION_SAMPLES_DIR = Path(
    os.getenv("VISION_SAMPLES_DIR", str(DATA_ROOT / "vision_samples"))
).resolve()
VISION_SAMPLES_MAX_PER_RUN = int(os.getenv("VISION_SAMPLES_MAX_PER_RUN", "200"))

# Platform monitoring agent (LangChain + SQLite dedup)
PLATFORM_MAX_STEPS = int(os.getenv("PLATFORM_MAX_STEPS", "90"))
PLATFORM_MAX_NEW_TENDERS = int(os.getenv("PLATFORM_MAX_NEW_TENDERS", "3"))
# Max document files to download per tender (priority docs; skip junk/wrappers)
PLATFORM_MAX_FILES_PER_TENDER = int(os.getenv("PLATFORM_MAX_FILES_PER_TENDER", "5"))
# platform (default) | browser (ablation only). "full" is rejected.
PLATFORM_AGENT_MODE = os.getenv("PLATFORM_AGENT_MODE", "platform").strip().lower()
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
