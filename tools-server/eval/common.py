"""Общие пути, LLM-клиент и нормализация текста для стенда оценки извлечения и RAG."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
TOOLS_DIR = EVAL_DIR.parent
REPO = TOOLS_DIR.parent
OUT = EVAL_DIR / "out"
OUT.mkdir(exist_ok=True)

CORPUS_ROOTS = [
    REPO / "data" / "tenders",
    Path(os.environ.get("LOCALAPPDATA", "")) / "AISmartTender" / "data" / "tenders",
    REPO / "data" / "catalogs",
]

# app.config читает DATA_ROOT при импорте; индексу страниц нужен корень, под которым лежит файл.
os.environ.setdefault("DATA_ROOT", tempfile.mkdtemp(prefix="eval-data-"))
os.environ.setdefault("TOOLS_OCR_MODEL_DIR", "")
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def load_env() -> None:
    for f in (REPO / ".env", REPO / "tender-agent" / ".env"):
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", line)
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2).strip().strip('"')


load_env()

def sniff(path: Path) -> str:
    from app.document_tool.sniff import sniff_path

    return sniff_path(path)


def sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


_WS = re.compile(r"\s+")
_PUNCT_SP = re.compile(r"\s*([.,;:!?()«»\"'/\\|\-–—№%])\s*")


def norm(text: str) -> str:
    """Для сравнения цитат: регистр, ё, пробелы и пробелы вокруг пунктуации не важны."""
    t = (text or "").lower().replace("ё", "е").replace("\xa0", " ")
    t = t.replace("|", " ")
    t = _PUNCT_SP.sub(r"\1", t)
    return _WS.sub(" ", t).strip()


def llm_client():
    from openai import OpenAI

    base = os.environ.get("AGENT_LLM_BASE_URL") or os.environ.get("TENDER_AGENT_LLM_BASE_URL")
    key = os.environ.get("AGENT_LLM_API_KEY") or os.environ.get("TENDER_AGENT_LLM_API_KEY")
    model = os.environ.get("EVAL_LLM_MODEL") or os.environ.get("AGENT_LLM_MODEL") or "deepseek-chat"
    return OpenAI(base_url=base, api_key=key, timeout=180), model


def llm_json(messages: list[dict], temperature: float = 0.0, max_tokens: int = 4000) -> dict:
    client, model = llm_client()
    for attempt in range(3):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            return json.loads(r.choices[0].message.content)
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  llm retry: {e}")
    return {}


def write_jsonl(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
