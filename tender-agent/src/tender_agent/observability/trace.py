"""Трейс одного прогона для разбора ошибок на реальных площадках.

Папка ``<data_dir>/_agent/runs/<время>-<хост>/``:

- ``task.json`` — исходный текст задания и разобранный TaskSpec;
- ``steps.jsonl`` — по строке на шаг: адрес, тип страницы, что агент увидел
  (поиск, сортировка, «далее», карточки, поля), выбранное действие и его итог;
- ``llm.jsonl`` — вызовы модели: какой промпт, размер наблюдения, сырой ответ;
- ``step_XX.png`` — скриншоты (все шаги или только ошибки, см. TENDER_AGENT_TRACE_SCREENSHOTS);
- ``page_XX.txt`` — текст основного блока страницы на шагах с ошибкой;
- ``result.json`` — итоговый ответ агента;
- ``vision/`` — трейсы vision-шагов ai_vision, если они были.

Плюс общий ``<data_dir>/_agent/runs/index.jsonl`` — строка на прогон: удобно
отфильтровать неудачи по площадкам и открыть нужную папку.
"""

from __future__ import annotations

import json
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

current_trace: ContextVar["RunTrace | None"] = ContextVar("tender_agent_trace", default=None)


def _safe(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in text)[:60]


class RunTrace:
    def __init__(self, root: Path, url: str, text: str, screenshots: str = "errors") -> None:
        self.root = root
        host = urlparse(url).hostname or "unknown"
        self.dir = root / f"{time.strftime('%Y%m%d-%H%M%S')}-{_safe(host)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.host = host
        self.text = text
        self.screenshots = screenshots  # "all" | "errors" | "none"
        self.started = time.monotonic()
        self.step = 0
        self._write("task.json", {"text": text, "url": url})

    # ---- запись ----

    def _write(self, name: str, data: object) -> None:
        (self.dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def _append(self, name: str, record: dict) -> None:
        with (self.dir / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def task(self, spec: object) -> None:
        data = spec.model_dump() if hasattr(spec, "model_dump") else spec
        self._write("task.json", {"text": self.text, "spec": data})

    def log_step(self, record: dict) -> None:
        self.step += 1
        self._append("steps.jsonl", {"step": self.step, "t": round(time.monotonic() - self.started, 2), **record})

    def log_llm(self, kind: str, observation: dict, raw: str, error: str | None = None) -> None:
        self._append("llm.jsonl", {
            "step": self.step,
            "kind": kind,
            "observation_keys": sorted(observation.keys()),
            "observation_chars": len(json.dumps(observation, ensure_ascii=False)),
            "observation": observation,
            "raw": raw,
            "error": error,
        })

    async def snapshot(self, page, *, error: bool, text: str = "") -> None:
        """Скриншот и текст страницы текущего шага (по настройке)."""
        if self.screenshots == "none" or (self.screenshots == "errors" and not error):
            return
        try:
            await page.screenshot(path=str(self.dir / f"step_{self.step:02d}.png"))
        except Exception:
            pass
        if error and text:
            (self.dir / f"page_{self.step:02d}.txt").write_text(text, encoding="utf-8")

    def vision_dir(self) -> Path:
        path = self.dir / "vision"
        path.mkdir(exist_ok=True)
        return path

    def finish(self, result: object) -> None:
        data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        self._write("result.json", data)
        summary = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": self.host,
            "task": self.text,
            "status": data.get("status"),
            "results": len(data.get("results") or []),
            "errors": data.get("errors") or [],
            "stats": data.get("stats") or {},
            "seconds": round(time.monotonic() - self.started, 1),
            "trace": str(self.dir),
        }
        with (self.root / "index.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
