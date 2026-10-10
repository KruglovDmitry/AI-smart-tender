from __future__ import annotations

from .state import Action, Progress


def action_key(action: Action, signature: str) -> str:
    return f"{action.tool}:{action.element_ref}:{action.note}:{signature}"


def should_stop_repeat(progress: Progress, action: Action, signature: str, max_retries: int) -> bool:
    key = action_key(action, signature)
    return progress.failures.get(key, 0) >= max_retries


def note_failure(progress: Progress, action: Action, signature: str) -> None:
    key = action_key(action, signature)
    progress.failures[key] = progress.failures.get(key, 0) + 1


def clear_failure(progress: Progress, action: Action, signature: str) -> None:
    progress.failures.pop(action_key(action, signature), None)
