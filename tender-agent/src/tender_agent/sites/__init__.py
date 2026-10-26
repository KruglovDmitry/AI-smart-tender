"""Заметки по знакомым площадкам: как устроены выдача и карточка, известные ловушки.

Файл на площадку (``notes/<домен>.md``): шапка ``---`` с ``domain``, ``also`` (другие домены той же
площадки) и ``updated``, затем разделы ``## Общее``, ``## Выдача``, ``## Карточка``, ``## Ловушки``.
Базовые заметки едут в сборке; свои можно положить в ``<data_dir>/_agent/sites/`` — файл с тем же
доменом заменяет базовый целиком.

Модель получает заметки только текущего сайта и этапа (выдача или карточка) — это подсказки,
а не правила: сайты меняются, страница важнее заметки. К ним добавляются действующие пункты,
выведенные из прогонов (``learning.py``), с пометкой «авто».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

BUILTIN_DIR = Path(__file__).with_name("notes")
MAX_CHARS = 1_500  # на этап; длинная заметка — это уже инструкция, а не подсказка

# Разделы, которые видит модель на каждом этапе.
_PHASE_SECTIONS = {"list": ("Общее", "Выдача", "Ловушки"), "card": ("Общее", "Карточка", "Ловушки")}


@dataclass
class SiteNotes:
    domains: tuple[str, ...]
    updated: str = ""
    sections: dict[str, list[str]] = field(default_factory=dict)
    source: str = ""

    def lines(self, phase: str) -> list[str]:
        return [f"[{name}] {item}" for name in _PHASE_SECTIONS.get(phase, ())
                for item in self.sections.get(name, [])]

    def text(self, phase: str) -> str:
        return "\n".join(self.lines(phase))[:MAX_CHARS]


def parse(text: str, source: str = "") -> SiteNotes | None:
    head = re.match(r"\s*---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not head:
        return None
    meta = dict(re.findall(r"^\s*(\w+)\s*:\s*(.*?)\s*$", head.group(1), re.M))
    domains = [meta.get("domain", "")] + re.split(r"[,\s]+", meta.get("also", ""))
    domains = tuple(d.strip().lower().removeprefix("www.") for d in domains if d.strip())
    if not domains:
        return None
    notes = SiteNotes(domains=domains, updated=meta.get("updated", ""), source=source)
    current: list[str] | None = None
    for line in text[head.end():].splitlines():
        title = re.match(r"\s*##\s+(.+?)\s*$", line)
        if title:
            current = notes.sections.setdefault(title.group(1), [])
        elif current is not None and re.match(r"\s*[-*]\s+\S", line):
            current.append(re.sub(r"^\s*[-*]\s+", "", line).strip())
        elif current and line.strip():
            current[-1] += " " + line.strip()  # перенос строки внутри пункта
    return notes


def load(extra_dir: Path | None = None) -> list[SiteNotes]:
    """Базовые заметки и свои из extra_dir; свои заменяют базовые с тем же основным доменом."""
    found: dict[str, SiteNotes] = {}
    for folder in (BUILTIN_DIR, extra_dir):
        if folder is None or not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            try:
                notes = parse(path.read_text(encoding="utf-8"), str(path))
            except (OSError, UnicodeDecodeError):
                continue
            if notes is not None:
                found[notes.domains[0]] = notes
    return list(found.values())


def notes_for(url: str, phase: str, extra_dir: Path | None = None) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return ""
    found = next((notes for notes in load(extra_dir)
                  if any(host == d or host.endswith("." + d) for d in notes.domains)), None)
    lines = found.lines(phase) if found else []
    if extra_dir is not None:
        from .learning import CandidateStore, _norm, notes_lines  # noqa: PLC0415

        add, remove = notes_lines(found.domains[0] if found else host, CandidateStore(extra_dir), phase)
        # Пункт, который прогоны признали устаревшим, модель больше не видит; выведенные — после заметки.
        lines = [line for line in lines if _norm(line.split("] ", 1)[-1]) not in remove] + add
    return "\n".join(lines)[:MAX_CHARS]
