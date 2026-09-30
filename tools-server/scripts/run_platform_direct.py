# -*- coding: utf-8 -*-
"""Direct in-process platform run (avoids HTTP ~600s 503 on long tasks)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# tools-server on path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATA = ROOT / "data"
run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
label = os.environ.get("RUN_LABEL", "qwen-kanctovary")
subdir = f"runs_{run_id}-{label}".replace("/", "_")
out_json = DATA / "_logs" / "agent" / f"{run_id}-{label}-response.json"
out_json.parent.mkdir(parents=True, exist_ok=True)

from app.agent.loop import run_platform_task  # noqa: E402


async def main() -> int:
    print(f"RUN={run_id} subdir={subdir} model={os.environ.get('AGENT_PRIMARY_MODEL')}", flush=True)
    data = await run_platform_task(
        platform_url="https://zakupki.gov.ru/",
        keywords="канцтовары",
        max_new_tenders=10,
        max_steps=180,
        download_subdir=subdir,
        tools_mode="platform",
        instruction=(
            "Поиск только по ключевому слову «канцтовары», без фильтров. "
            "Обработай до 10 новых закупок: open_platform_search → list_new_cards → "
            "open_tender → save_overview → list_tender_documents → download_document → "
            "mark_processed → finish. Используй DOM (dom_snapshot / click_element). "
            "Если list_tender_documents пуст — DOM/vision по вкладке «Документы», затем list снова."
        ),
    )
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved={out_json}", flush=True)
    print("success", data.get("success"), flush=True)
    print("processed", data.get("new_tenders_processed"), flush=True)
    print("steps", data.get("steps"), flush=True)
    print("usage", json.dumps(data.get("usage"), ensure_ascii=False), flush=True)
    print("wall", data.get("wall_time_s"), flush=True)
    print("log", data.get("agent_log_path"), flush=True)
    print("files", len(data.get("downloaded_files") or []), flush=True)
    print("downloads_dir", data.get("downloads_dir"), flush=True)
    return 0 if data.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
