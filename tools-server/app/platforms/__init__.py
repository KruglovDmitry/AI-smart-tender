"""Pluggable tender-platform adapters."""

from .base import CardRef, DocRef, GenericAdapter, PlatformAdapter, SearchSpec, StepResult
from .registry import get_adapter, list_adapters, register
from .zakupki_gov_ru import ZakupkiGovRuAdapter

__all__ = [
    "CardRef",
    "DocRef",
    "GenericAdapter",
    "PlatformAdapter",
    "SearchSpec",
    "StepResult",
    "ZakupkiGovRuAdapter",
    "get_adapter",
    "list_adapters",
    "register",
]
