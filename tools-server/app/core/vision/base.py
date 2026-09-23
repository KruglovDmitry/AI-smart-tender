"""Vision backend protocol and result dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class GroundingCandidate:
    x: int
    y: int
    label: str = ""


@dataclass
class GroundingResult:
    found: bool
    candidates: list[GroundingCandidate] = field(default_factory=list)
    backend: str = ""
    model: str = ""
    note: str = ""
    latency_ms: int = 0
    raw: dict[str, Any] | None = None


@dataclass
class InspectionResult:
    answer: str
    backend: str = ""
    model: str = ""
    latency_ms: int = 0


@runtime_checkable
class VisionBackend(Protocol):
    name: str

    async def ground(
        self,
        image_b64: str,
        target: str,
        viewport: tuple[int, int],
    ) -> GroundingResult: ...

    async def inspect(
        self,
        image_b64: str,
        question: str,
        viewport: tuple[int, int],
    ) -> InspectionResult: ...
