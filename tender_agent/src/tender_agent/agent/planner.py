from __future__ import annotations

import re

from ..api.schemas import TaskSpec
from ..config import Settings
from ..llm.base import LLMClient
from ..heuristics.patterns import FILE_RE
from ..tenders.models import Observation
from .state import Action

_URL = re.compile(r"https?://[^\s,;]+", re.I)
_QUOTED = re.compile(r"[\"«](.+?)[\"»]")
_NUM_WORDS = {
    "один": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
}


class TaskParseError(ValueError):
    pass


def _limit_token(text: str, default: int) -> int:
    match = re.search(r"(?:последн\w+|перв\w+)\s+(\d+|[а-яё]+)", text, re.I)
    if not match:
        match = re.search(r"(\d+)\s+закуп", text, re.I)
    if not match:
        return default
    token = match.group(1).casefold()
    if token.isdigit():
        return max(1, int(token))
    return _NUM_WORDS.get(token, default)


def parse_task(text: str, settings: Settings) -> TaskSpec:
    raw = (text or "").strip()
    if not raw:
        raise TaskParseError("Пустое задание.")
    found = _URL.search(raw)
    if not found:
        raise TaskParseError("В задании нет URL площадки.")
    url = found.group(0).rstrip(".,)")
    quoted = [item.strip() for item in _QUOTED.findall(raw) if item.strip()]
    topic = quoted[0] if quoted else ""
    if not topic:
        themed = re.search(r"на тему\s+(.+?)\s*$", raw, re.I)
        if themed:
            topic = themed.group(1).strip(" «».")
    if not topic:
        raise TaskParseError("Не удалось однозначно определить ключевое слово или тему.")
    sort_by = "published_at" if re.search(r"последн|дат[аеуы]\s+публикац", raw, re.I) else None
    filters: dict[str, str] = {}
    if re.search(r"фильтр по дате|дат[аеуы]\s+публикац", raw, re.I):
        filters["published_at"] = "latest"
        sort_by = "published_at"
    return TaskSpec(
        url=url,
        topic=topic,
        keywords=[topic],
        limit=_limit_token(raw, settings.default_limit),
        sort_by=sort_by,  # type: ignore[arg-type]
        filters=filters,
        download_documents=bool(re.search(r"скач", raw, re.I)),
        max_pages=settings.max_pages,
        max_actions=settings.max_actions,
        timeout_seconds=settings.timeout_seconds,
    )


def _document_elements(obs: Observation) -> list[dict]:
    documents = []
    for el in obs.elements:
        blob = f"{el.name} {el.href or ''}"
        if el.ref in obs.file_refs or el.download or FILE_RE.search(el.href or "") or FILE_RE.search(el.name or ""):
            documents.append(
                {"ref": el.ref, "name": el.name[:160], "href": (el.href or "")[:200]}
            )
        elif re.search(r"документ|скачать|файл|вложен", blob, re.I):
            documents.append(
                {"ref": el.ref, "name": el.name[:160], "href": (el.href or "")[:200]}
            )
        if len(documents) >= 30:
            break
    return documents


def _nav_links(obs: Observation, documents: list[dict]) -> list[dict]:
    """Короткие ссылки страницы (вкладки, разделы) — среди них модель ищет «Документы»."""
    taken = {doc["ref"] for doc in documents}
    links = []
    for el in obs.elements:
        name = (el.name or "").strip()
        if el.ref in taken or not el.href or not name or len(name) > 60:
            continue
        links.append({"ref": el.ref, "name": name[:60], "href": el.href[:160]})
        if len(links) >= 40:
            break
    return links


def card_for_model(obs: Observation) -> dict:
    """Пары полей, основной текст страницы (без шапки и меню), ссылки на файлы и прочие ссылки."""
    documents = _document_elements(obs)
    return {
        "url": obs.url,
        "title": obs.title,
        "fields": [
            {"id": field.id, "label": field.label[:90], "value": field.value[:300]}
            for field in obs.fields[:80]
        ],
        "content": (obs.content_text or obs.text_excerpt)[:12000],
        "documents": documents,
        "links": _nav_links(obs, documents),
    }


def observation_for_model(obs: Observation) -> dict:
    return {
        "url": obs.url,
        "title": obs.title,
        "page_kind": obs.page_kind,
        "headings": obs.headings[:6],
        "text_excerpt": obs.text_excerpt[:800],
        "elements": [
            {
                "ref": el.ref,
                "tag": el.tag,
                "role": el.role,
                "name": el.name[:120],
                "href": (el.href or "")[:180],
                "placeholder": el.placeholder or "",
            }
            for el in obs.elements[:40]
        ],
    }


async def read_card(llm: LLMClient | None, task: TaskSpec, obs: Observation) -> dict | None:
    method = getattr(llm, "read_card", None)
    if llm is None or method is None:
        return None
    data = await method(task.model_dump(), card_for_model(obs))
    if not isinstance(data, dict):
        return None
    return data


async def consult(llm: LLMClient | None, task: TaskSpec, obs: Observation) -> Action | None:
    if llm is None:
        return None
    data = await llm.decide(task.model_dump(), observation_for_model(obs))
    tool = str(data.get("tool") or "")
    if tool not in {"click", "fill", "finish", "scroll"}:
        return None
    ref = data.get("element_ref")
    if ref is not None:
        try:
            ref = int(ref)
        except (TypeError, ValueError):
            return None
        if ref not in {el.ref for el in obs.elements}:
            return None
    return Action(
        tool=tool,  # type: ignore[arg-type]
        element_ref=ref,
        value=data.get("value"),
        expected=str(data.get("expected") or "changed"),
        note="llm",
    )
