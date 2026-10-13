from __future__ import annotations

import os
from pathlib import Path

# tools-server/app/config.py → repo root (AI-smart-tender)
_REPO_ROOT = Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env", override=False)
except ImportError:
    pass

DATA_ROOT = Path(os.getenv("DATA_ROOT", str(_REPO_ROOT / "data"))).resolve()
HOST = os.getenv("TOOLS_HOST", "0.0.0.0")
PORT = int(os.getenv("TOOLS_PORT", "8000"))
TOOLS_PUBLIC_BASE_URL = os.getenv("TOOLS_PUBLIC_BASE_URL", "").rstrip("/")

DEFAULT_MAX_CHARS = int(os.getenv("DEFAULT_MAX_CHARS", "120000"))
DEFAULT_MAX_FILES = int(os.getenv("DEFAULT_MAX_FILES", "30"))
FETCH_TIMEOUT_SEC = float(os.getenv("FETCH_TIMEOUT_SEC", "30"))

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
