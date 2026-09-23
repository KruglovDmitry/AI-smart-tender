"""
UI-TARS Stage-A grounding eval (Mode A only).

Flow:
  Playwright screenshot + getBoundingClientRect() ground truth
       → UI-TARS GROUNDING
       → core parser (image_w/h)
       → hit@bbox / distance / distance_norm / false_positive / latency

Live tasks are summarized separately from fixtures.

Usage:
  py scripts/eval_ui_tars/run_eval.py --tasks scripts/eval_ui_tars/tasks_v2.json --skip-live
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from struct import unpack
from typing import Any

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from annotate import annotate_screenshot  # noqa: E402
from client import DEFAULT_BASE, DEFAULT_MODEL, UiTarsClient  # noqa: E402
from coords import bbox_dict_to_xyxy, score_prediction  # noqa: E402

FIXTURES = ROOT / "fixtures"
TASKS_PATH = ROOT / "tasks_v2.json"
DATA_ROOT = ROOT.parents[1] / "data" / "ui_tars_eval"

_BBOX_JS = """(el) => {
  const r = el.getBoundingClientRect();
  return {
    x: Math.round(r.x),
    y: Math.round(r.y),
    width: Math.round(r.width),
    height: Math.round(r.height),
  };
}"""


def _bbox_from_locator(loc) -> dict[str, int] | None:
    """Ground-truth bbox via element.getBoundingClientRect() (CSS viewport)."""
    try:
        box = loc.evaluate(_BBOX_JS)
    except Exception:
        box = None
    if not box:
        # fallback Playwright box
        pb = loc.bounding_box()
        if not pb:
            return None
        box = {
            "x": int(pb["x"]),
            "y": int(pb["y"]),
            "width": int(pb["width"]),
            "height": int(pb["height"]),
        }
    if int(box.get("width") or 0) <= 0 or int(box.get("height") or 0) <= 0:
        return None
    return {
        "x": int(box["x"]),
        "y": int(box["y"]),
        "width": int(box["width"]),
        "height": int(box["height"]),
    }


def _first_visible(page, selectors: list[str]):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0 or not loc.is_visible():
                continue
            return loc, sel
        except Exception:
            continue
    return None, None


def _percentile(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return float(s[f] + (s[c] - s[f]) * (k - f))


def run_task(page, client: UiTarsClient, task: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    tid = task["id"]
    goal = task["goal"]
    expect_nf = bool(task.get("expect_not_found"))
    source = task.get("source") or "fixture"
    row: dict[str, Any] = {
        "task_id": tid,
        "scenario": task.get("scenario") or "",
        "source": source,
        "platform": task.get("platform") or task.get("fixture") or "",
        "goal": goal,
        "expected_element": task.get("expected_element"),
        "expect_not_found": expect_nf,
        "screenshot": None,
        "annotated_screenshot": None,
        "expected_bbox": None,
        "predicted_x": None,
        "predicted_y": None,
        "distance_px": None,
        "distance_norm": None,
        "hit_bbox": None,
        "false_positive": False,
        "success": False,
        "latency_ms": None,
        "raw_model_response": "",
        "action_pred": None,
        "skipped": False,
    }

    try:
        if source == "fixture":
            page.goto(
                (FIXTURES / task["fixture"]).resolve().as_uri(),
                wait_until="domcontentloaded",
            )
            if task.get("setup_js"):
                page.evaluate(task["setup_js"])
            if task.get("wait_ms"):
                page.wait_for_timeout(int(task["wait_ms"]))
            loc, used_sel = None, None
            if not expect_nf:
                loc = page.locator(task["selector"]).first
                used_sel = task["selector"]
        else:
            page.goto(task["url"], wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1500)
            loc, used_sel = _first_visible(page, task.get("selector_candidates") or [])
            if loc is None and not expect_nf:
                row["skipped"] = True
                row["skip_reason"] = "selector_not_found"
                return row

        bbox_dict = None
        if loc is not None:
            try:
                loc.scroll_into_view_if_needed(timeout=5_000)
                page.wait_for_timeout(200)
            except Exception:
                pass
            bbox_dict = _bbox_from_locator(loc)
            if bbox_dict is None and not expect_nf:
                row["skipped"] = True
                row["skip_reason"] = "no_bbox"
                return row

        vp = page.viewport_size or {"width": 1280, "height": 900}
        viewport = (int(vp["width"]), int(vp["height"]))
        png = page.screenshot(type="png", full_page=False)
        iw, ih = unpack(">II", png[16:24])
        image_size = (int(iw), int(ih))

        shot_name = f"{tid}.png"
        ann_name = f"{tid}_annotated.png"
        (out_dir / shot_name).write_bytes(png)

        api = client.ground(png, goal)
        content = api.get("content") or ""
        row["raw_model_response"] = content
        row["latency_ms"] = api.get("latency_ms")
        row["api_ok"] = api.get("ok")
        row["selector"] = used_sel
        row["url"] = page.url
        row["viewport"] = list(viewport)
        row["image_size"] = list(image_size)
        row["screenshot"] = shot_name

        xyxy = bbox_dict_to_xyxy(bbox_dict) if bbox_dict else None
        if bbox_dict:
            row["expected_bbox"] = bbox_dict

        scored = score_prediction(
            content,
            xyxy,
            viewport=viewport,
            image_size=image_size,
            expect_not_found=expect_nf,
        )
        row["action_pred"] = scored.get("action")
        row["hit_bbox"] = scored.get("hit_bbox")
        row["distance_px"] = scored.get("distance_px")
        row["distance_norm"] = scored.get("distance_norm")
        row["false_positive"] = bool(scored.get("false_positive"))
        row["success"] = bool(scored.get("success"))
        row["parse_note"] = scored.get("parse_note")
        pred = scored.get("pred_css")
        if pred:
            row["predicted_x"], row["predicted_y"] = pred[0], pred[1]

        # annotate expects xyxy bbox + pred point
        annotate_screenshot(
            png,
            bbox=xyxy,
            pred=tuple(pred) if pred else None,
            out_path=out_dir / ann_name,
            label=(task.get("expected_element") or goal)[:40],
        )
        row["annotated_screenshot"] = ann_name

        (out_dir / f"{tid}.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return row
    except Exception as e:
        row["skipped"] = True
        row["skip_reason"] = f"error: {e}"
        row["traceback"] = traceback.format_exc()[-1500:]
        return row


def _metrics_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ran = [r for r in rows if not r.get("skipped")]
    with_bbox = [r for r in ran if not r.get("expect_not_found")]
    nf = [r for r in ran if r.get("expect_not_found")]
    hits = [r for r in with_bbox if r.get("hit_bbox")]
    dists = [
        float(r["distance_px"])
        for r in with_bbox
        if isinstance(r.get("distance_px"), (int, float))
    ]
    norms = [
        float(r["distance_norm"])
        for r in with_bbox
        if isinstance(r.get("distance_norm"), (int, float))
    ]
    lats = [float(r["latency_ms"]) for r in ran if isinstance(r.get("latency_ms"), int)]
    fp = sum(1 for r in nf if r.get("false_positive"))
    return {
        "ran": len(ran),
        "skipped": len(rows) - len(ran),
        "grounding_tasks": len(with_bbox),
        "element_hit": f"{len(hits)}/{len(with_bbox)}" if with_bbox else "0/0",
        "hit_bbox_rate": round(len(hits) / len(with_bbox), 3) if with_bbox else None,
        "false_positive": f"{fp}/{len(nf)}" if nf else "0/0",
        "false_positive_count": fp,
        "not_found_correct": sum(1 for r in nf if r.get("success")),
        "not_found_tasks": len(nf),
        "distance_px_median": _percentile(dists, 0.5),
        "distance_px_p95": _percentile(dists, 0.95),
        "distance_norm_median": _percentile(norms, 0.5),
        "latency_ms_p50": _percentile(lats, 0.5),
        "latency_ms_p95": _percentile(lats, 0.95),
        "by_scenario": _by_scenario(ran),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fixture = [r for r in rows if r.get("source") != "live"]
    live = [r for r in rows if r.get("source") == "live"]
    return {
        "total_tasks": len(rows),
        "fixture": _metrics_block(fixture),
        "live": _metrics_block(live) if live else None,
        "all": _metrics_block(rows),
        "table": [
            {
                "task_id": r["task_id"],
                "source": r.get("source"),
                "scenario": r.get("scenario"),
                "hit_bbox": r.get("hit_bbox"),
                "distance_px": r.get("distance_px"),
                "distance_norm": r.get("distance_norm"),
                "false_positive": r.get("false_positive"),
                "latency_ms": r.get("latency_ms"),
                "success": r.get("success"),
                "action_pred": r.get("action_pred"),
            }
            for r in rows
            if not r.get("skipped")
        ],
    }


def _by_scenario(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for r in rows:
        sc = r.get("scenario") or "?"
        out.setdefault(sc, {"n": 0, "hit": 0, "dists": [], "fp": 0})
        out[sc]["n"] += 1
        if r.get("hit_bbox"):
            out[sc]["hit"] += 1
        if isinstance(r.get("distance_px"), (int, float)):
            out[sc]["dists"].append(float(r["distance_px"]))
        if r.get("false_positive"):
            out[sc]["fp"] += 1
    for sc, v in out.items():
        v["hit_rate"] = round(v["hit"] / v["n"], 3) if v["n"] else None
        v["dist_median"] = _percentile(v["dists"], 0.5)
        del v["dists"]
    return out


def _print_block(title: str, m: dict[str, Any] | None) -> None:
    if not m:
        return
    print(f"\n--- {title} ---")
    print(
        f"element hit       {m.get('element_hit')}\n"
        f"hit@bbox          {m.get('hit_bbox_rate')}\n"
        f"false positive    {m.get('false_positive')}\n"
        f"median distance   {m.get('distance_px_median')} px\n"
        f"p95 distance      {m.get('distance_px_p95')} px\n"
        f"median dist_norm  {m.get('distance_norm_median')}\n"
        f"p50 latency       {m.get('latency_ms_p50')} ms\n"
        f"p95 latency       {m.get('latency_ms_p95')} ms"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--tasks", type=Path, default=TASKS_PATH)
    ap.add_argument("--skip-live", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    tasks = json.loads(args.tasks.read_text(encoding="utf-8"))
    if args.skip_live:
        tasks = [t for t in tasks if t.get("source") != "live"]
    if args.only:
        want = set(args.only)
        tasks = [t for t in tasks if t["id"] in want]

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = DATA_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    client = UiTarsClient(base_url=args.base_url, model=args.model)
    rows: list[dict[str, Any]] = []

    print(f"UI-TARS Mode A (GROUNDING) → {args.base_url} model={args.model}")
    print(f"tasks={len(tasks)} out={out_dir}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            device_scale_factor=1,
            locale="ru-RU",
        )
        page = context.new_page()
        for i, task in enumerate(tasks, 1):
            print(
                f"[{i}/{len(tasks)}] {task['id']} "
                f"({task.get('scenario')}/{task.get('source')}) …",
                flush=True,
            )
            row = run_task(page, client, task, out_dir)
            rows.append(row)
            if row.get("skipped"):
                print(f"  SKIP {row.get('skip_reason')}")
            else:
                print(
                    f"  hit={row.get('hit_bbox')} dist={row.get('distance_px')} "
                    f"norm={row.get('distance_norm')} fp={row.get('false_positive')} "
                    f"lat={row.get('latency_ms')}ms action={row.get('action_pred')} "
                    f"ok={row.get('success')}"
                )
        browser.close()

    summary = summarize(rows)
    report = {
        "run_id": run_id,
        "contract": "mode_A_ui_tars_grounding",
        "base_url": args.base_url,
        "model": args.model,
        "summary": summary,
        "results": rows,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "task_id\tsource\tscenario\thit_bbox\tdistance_px\tdistance_norm\t"
        "false_positive\tlatency_ms\tsuccess\taction_pred\tgoal"
    ]
    for r in rows:
        if r.get("skipped"):
            lines.append(
                f"{r['task_id']}\t{r.get('source')}\t{r.get('scenario')}\tskip\t\t\t\t\t\t\t{r.get('goal')}"
            )
            continue
        lines.append(
            f"{r['task_id']}\t{r.get('source')}\t{r.get('scenario')}\t{r.get('hit_bbox')}\t"
            f"{r.get('distance_px')}\t{r.get('distance_norm')}\t{r.get('false_positive')}\t"
            f"{r.get('latency_ms')}\t{r.get('success')}\t{r.get('action_pred')}\t{r.get('goal')}"
        )
    (out_dir / "results.tsv").write_text("\n".join(lines), encoding="utf-8")

    print("\n=== SUMMARY ===")
    _print_block("fixture", summary.get("fixture"))
    _print_block("live (separate)", summary.get("live"))
    print(f"\nAnnotated shots: {out_dir}/*_annotated.png")
    print(f"report: {out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
