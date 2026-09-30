# -*- coding: utf-8 -*-
"""One-shot local run: platform task канцтовары x10 (Qwen)."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
subdir = f"runs/{run_id}-qwen-kanctovary"
out_json = DATA / "_logs" / "agent" / f"{run_id}-qwen-kanctovary-response.json"
out_json.parent.mkdir(parents=True, exist_ok=True)
(DATA / "tenders" / "runs").mkdir(parents=True, exist_ok=True)

body = {
    "platform_url": "https://zakupki.gov.ru/",
    "keywords": "канцтовары",
    "max_new_tenders": 10,
    "max_steps": 180,
    "download_subdir": subdir,
    "tools_mode": "platform",
    "instruction": (
        "Поиск только по ключевому слову «канцтовары», без фильтров. "
        "Обработай до 10 новых закупок: open_platform_search → list_new_cards → "
        "open_tender → save_overview → list_tender_documents → download_document → "
        "mark_processed → finish. Предпочитай DOM; click_on_screen только если DOM не помогает."
    ),
}

print(f"RUN={run_id} subdir={subdir}", flush=True)
print("POST /run_platform_task ...", flush=True)
with httpx.Client(timeout=None) as client:
    r = client.post("http://127.0.0.1:8000/run_platform_task", json=body)
print(f"http={r.status_code}", flush=True)
out_json.write_text(r.text, encoding="utf-8")
print(f"saved={out_json}", flush=True)
if r.status_code >= 400:
    print(r.text[:2000], flush=True)
    sys.exit(1)
data = r.json()
print("success", data.get("success"), flush=True)
print("processed", data.get("new_tenders_processed"), flush=True)
print("steps", data.get("steps"), flush=True)
print("usage", json.dumps(data.get("usage"), ensure_ascii=False), flush=True)
print("wall", data.get("wall_time_s"), flush=True)
print("log", data.get("agent_log_path"), flush=True)
print("debug", data.get("debug_json"), flush=True)
print("files", len(data.get("downloaded_files") or []), flush=True)
print("downloads_dir", data.get("downloads_dir"), flush=True)
