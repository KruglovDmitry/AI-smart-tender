"""Заполнить базу закупок из трейсов уже сделанных прогонов.

    python -m tender_agent.storage.backfill            # все прогоны из data/tenders/_agent/runs
    python -m tender_agent.storage.backfill --dry-run  # только посчитать

Трейс прогона (``task.json`` + ``result.json``) содержит всё, что агент вернул,
поэтому база восстанавливается без повторного обхода площадок. Прогоны, уже
загруженные в базу (по папке трейса), пропускаются.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from ..api.schemas import TaskResult
from ..config import Settings
from .tenders import TenderStore, search_runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Загрузить закупки из трейсов прогонов в базу")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    settings = Settings.from_env()
    store = TenderStore(settings.db_url or settings.data_dir / "_agent" / "agent.sqlite3")
    runs_dir = settings.data_dir / "_agent" / "runs"
    with store.engine.connect() as conn:
        loaded = {row.trace_dir for row in conn.execute(select(search_runs.c.trace_dir)) if row.trace_dir}

    added = tenders_new = 0
    for folder in sorted(p for p in runs_dir.glob("*") if p.is_dir()):
        result_file, task_file = folder / "result.json", folder / "task.json"
        if str(folder) in loaded or not result_file.is_file():
            continue
        result = TaskResult.model_validate(json.loads(result_file.read_text(encoding="utf-8")))
        text = json.loads(task_file.read_text(encoding="utf-8")).get("text", "") if task_file.is_file() else ""
        if result.task is None:
            continue
        if args.dry_run:
            print(f"{folder.name}: {len(result.results)} закупок")
            continue
        saved = store.save_run(text, result, str(folder))
        added += 1
        tenders_new += saved["new"]
        print(f"{folder.name}: {len(result.results)} закупок, новых {saved['new']}")
    print(f"Загружено прогонов: {added}, новых закупок: {tenders_new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
