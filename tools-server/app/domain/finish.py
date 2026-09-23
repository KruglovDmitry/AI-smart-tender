"""Structural success gate for finish()."""

from __future__ import annotations

from typing import Any


def _is_search_only(task_hint: str) -> bool:
    hint_l = (task_hint or "").lower()
    return any(
        x in hint_l
        for x in ("сценарий 2", "не открывай карточ", "не открывай карточки", "только поиск")
    )


def resolve_finish_success(
    requested_success: bool,
    *,
    processed_tenders: list[Any] | None,
    downloaded_files: list[Any] | None,
    task_hint: str = "",
    summary: str = "",
) -> tuple[bool, str]:
    """
    Structural success gate: success=True only with facts or search_only hint.
    Never upgrades success=False to True. Does not parse summary prose for claims.
    """
    summary = summary or ""
    if not requested_success:
        return False, summary

    has_facts = bool(processed_tenders) or bool(downloaded_files)
    if has_facts:
        return True, summary
    if _is_search_only(task_hint):
        return True, summary

    rejected = (
        "ОТКЛОНЕНО авто-проверкой: success=true без processed_tenders и без downloaded_files. "
        f"Было: {summary}"
    )
    return False, rejected
