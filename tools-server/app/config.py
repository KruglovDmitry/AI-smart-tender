from pathlib import Path
import os

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data")).resolve()
HOST = os.getenv("TOOLS_HOST", "0.0.0.0")
PORT = int(os.getenv("TOOLS_PORT", "8000"))

# Лимиты ответа tool'а (как у вложений в чат — текст целиком, но с потолком)
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
