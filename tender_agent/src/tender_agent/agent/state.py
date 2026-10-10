from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

Phase = Literal[
    "initializing",
    "observing",
    "planning",
    "executing",
    "verifying",
    "extracting",
    "downloading",
    "completed",
    "needs_user",
    "failed",
]


class Action(BaseModel):
    tool: Literal["fill", "click", "extract", "finish", "ask_user", "consult_llm", "scroll"]
    element_ref: int | None = None
    value: str | None = None
    expected: str = ""
    submit_ref: int | None = None
    note: str = ""


@dataclass
class Progress:
    phase: Phase = "initializing"
    keyword_applied: bool = False
    sort_applied: bool = False
    site_sort_used: bool = False
    extracted: list[str] = field(default_factory=list)
    accepted: int = 0
    pages: int = 0
    actions: int = 0
    llm_calls: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    visited: list[str] = field(default_factory=list)
    profile_rejected: bool = False
    search_placeholder: str = ""


class RunLimits(BaseModel):
    max_actions: int = 40
    max_llm_calls: int = 12
    max_retries: int = 2
    max_pages: int = 5
    notes: list[str] = Field(default_factory=list)
