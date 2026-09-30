# -*- coding: utf-8 -*-
import json
import sys
from collections import Counter
from pathlib import Path

run_token = sys.argv[1] if len(sys.argv) > 1 else "114114"
log = Path(r"c:\Users\Dima\Desktop\Work\AI-smart-tender\data\_logs\agent")
alls = sorted(log.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
found = None
for p in alls:
    if p.stat().st_size < 1000:
        continue
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    blob = str(d.get("downloads_dir") or "") + str(d.get("agent_log_path") or "")
    if run_token in blob:
        found = (p, d)
        break
if not found:
    print("NOT FOUND", run_token)
    sys.exit(1)
p, d = found
print("file", p.name)
print("success", d.get("success"))
print("processed", d.get("new_tenders_processed"), "/", d.get("max_new_tenders"))
print("steps", d.get("steps"), "wall", d.get("wall_time_s"))
print("model", d.get("model"))
print("usage", json.dumps(d.get("usage"), ensure_ascii=False))
tools = Counter(s.get("tool") for s in (d.get("trace") or []))
print("tools", dict(tools))
print(
    "vision",
    "click_on_screen",
    tools.get("click_on_screen", 0),
    "dom_snapshot",
    tools.get("dom_snapshot", 0),
)
dst = log / f"20260930-{run_token}-qwen-kanctovary-response.json"
# keep original name if response empty
alt = log / "20260930-114114-qwen-kanctovary-response.json"
alt.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print("saved", alt)

root = Path(d.get("downloads_dir") or "")
print("---folders---", root)
if root.is_dir():
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        files = [x.name for x in folder.iterdir() if x.is_file()]
        docs = [f for f in files if f not in ("overview.json", "manifest.json")]
        print(folder.name, "docs=", len(docs), "overview=", "overview.json" in files)
