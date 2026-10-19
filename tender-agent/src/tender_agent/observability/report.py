"""Сводка по прогонам из data/tenders/_agent/runs/index.jsonl.

    py -3 -m tender_agent.observability.report            # все прогоны
    py -3 -m tender_agent.observability.report --failed   # только неудачные, с путём к трейсу
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

from ..config import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сводка прогонов tender_agent по площадкам")
    parser.add_argument("--failed", action="store_true", help="Показать неудачные прогоны с путём к трейсу")
    parser.add_argument("--host", help="Только эта площадка")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    index = Settings.from_env().data_dir / "_agent" / "runs" / "index.jsonl"
    if not index.is_file():
        print(f"Прогонов пока нет: {index}")
        return 0
    runs = [json.loads(line) for line in index.open(encoding="utf-8") if line.strip()]
    if args.host:
        runs = [r for r in runs if args.host in r.get("host", "")]

    by_host: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        by_host[run.get("host", "?")].append(run)
    print(f"{'Площадка':<28} {'Прогонов':>8} {'completed':>9} {'partial':>8} {'needs_user':>10} {'failed':>7} {'vision':>7}")
    for host, items in sorted(by_host.items()):
        count = lambda status: sum(1 for r in items if r.get("status") == status)  # noqa: E731
        vision = sum(1 for r in items if (r.get("stats") or {}).get("vision_calls"))
        print(f"{host:<28} {len(items):>8} {count('completed'):>9} {count('partial'):>8} "
              f"{count('needs_user'):>10} {count('failed'):>7} {vision:>7}")

    if args.failed:
        print()
        for run in runs:
            if run.get("status") == "completed":
                continue
            print(f"[{run.get('status')}] {run.get('time')} {run.get('host')}: {run.get('task')}")
            for error in (run.get("errors") or [])[:4]:
                print(f"    - {error[:200]}")
            print(f"    трейс: {run.get('trace')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
