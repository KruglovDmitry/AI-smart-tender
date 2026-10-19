from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Progress:
    """Счётчики задания для stats и дашборда."""

    actions: int = 0
    llm_calls: int = 0
    vision_calls: int = 0
    visited: list[str] = field(default_factory=list)
    # Учёт способов: {этап: {способ: {"used": n, "ok": m}}}.
    # Этапы: navigate (действия на странице), list (чтение выдачи).
    # Способы: llm (модель по DOM), vision (зрение).
    methods: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)

    def mark(self, stage: str, method: str, ok: bool) -> None:
        item = self.methods.setdefault(stage, {}).setdefault(method, {"used": 0, "ok": 0})
        item["used"] += 1
        if ok:
            item["ok"] += 1
