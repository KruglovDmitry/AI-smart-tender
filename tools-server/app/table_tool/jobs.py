"""Фоновая построчная обработка больших таблиц моделью.

Модель в чате не читает 13 тыс. строк: она один раз описывает задачу (какие колонки на входе, что заполнить,
по какому документу искать), а сервер сам идёт по таблице пачками, на каждую пачку делает короткий вызов
модели и пишет ответы в копию файла. В чат возвращаются прогресс, сводка и ссылка на файл.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import httpx
from openpyxl.utils import column_index_from_string

from ..config import DATA_ROOT
from ..document_tool import resolve_under_data
from . import _load, _pick_sheet, _resolve_column, _col_label, _blank, _headers, fill_table, _match

log = logging.getLogger(__name__)

MAX_JOB_ROWS = 50000
MAX_EXAMPLES = 6
LLM_TIMEOUT_SEC = float(os.getenv("TOOLS_LLM_TIMEOUT_SEC", "120"))
CONTEXT_CHARS_PER_BATCH = int(os.getenv("TOOLS_JOB_CONTEXT_CHARS", "40000"))
PAGE_CHARS = 9000
FLUSH_EVERY_SEC = 30

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _llm_config() -> tuple[str, str, str]:
    base = os.getenv("TOOLS_LLM_BASE_URL") or os.getenv("AGENT_LLM_BASE_URL") or ""
    key = os.getenv("TOOLS_LLM_API_KEY") or os.getenv("AGENT_LLM_API_KEY") or ""
    model = os.getenv("TOOLS_LLM_MODEL") or os.getenv("AGENT_LLM_MODEL") or ""
    if not (base and key and model):
        raise RuntimeError("LLM is not configured: set AGENT_LLM_BASE_URL, AGENT_LLM_API_KEY, AGENT_LLM_MODEL in .env")
    return base.rstrip("/"), key, model


def _chat(messages: list[dict[str, str]]) -> str:
    base, key, model = _llm_config()
    body = {"model": model, "messages": messages, "temperature": 0, "response_format": {"type": "json_object"}}
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = httpx.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=body,
                timeout=httpx.Timeout(LLM_TIMEOUT_SEC, connect=10),
            )
            if r.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM call failed: {last}")


def _parse_json(text: str) -> Any:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    return json.loads(text)


SYSTEM = """You fill spreadsheet rows. For every input row return values for the requested output columns.
Rules:
- Use only facts from the row itself and from the reference excerpts. Never invent technical data.
- If a column has a list of allowed values, answer exactly one of them, copied verbatim.
- If the answer cannot be determined, return null for that column and explain in "note".
- Catalog article numbers are often composed from a series code plus option digits; decode them with the
  coding/option tables in the excerpts.
Answer with JSON: {"rows": [{"row": <row number>, "values": {<column>: <value or null>}, "confidence": "high|medium|low", "note": "<short reason>"}]}"""


DIGEST_PROMPT = """Below are pages of a reference document (catalog). Write a compact reference card that lets someone
fill these spreadsheet columns for any item covered by these pages:
{outputs}
Task context: {instruction}
Include: product type names exactly as printed, ratings per type (current, voltage, contacts), how the article/order
code is composed (every position and what each value means: coil voltage codes, AC/DC digit, option codes), and
notes on exceptions. Plain text, at most 2500 characters. Return JSON: {{"card": "<text>"}}"""


def _digest(
    job: dict[str, Any], doc_path: str, series: str, pages: tuple[int, ...], locks: dict[Any, threading.Lock]
) -> str:
    """Сжатая «карточка серии» по страницам каталога: одна на задачу и серию, кэшируется рядом с индексом
    документа, поэтому следующие пачки и задания её не пересчитывают."""
    from .. import doc_index

    idx = doc_index.get_index(doc_path)
    spec = job["spec"]
    task_key = hashlib.sha1(json.dumps([spec["instruction"], spec["outputs"]], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:10]
    f = idx.cache / "digest" / f"{task_key}_{re.sub(r'[^0-9A-Za-zА-Яа-я]+', '_', series)}.txt"
    with _jobs_lock:
        lock = locks.setdefault(series, threading.Lock())
    with lock:
        if f.exists():
            return f.read_text(encoding="utf-8")
        text = "\n\n".join(f"=== page {p} ===\n{idx.text(p)[0][:PAGE_CHARS]}" for p in pages)
        outs = "\n".join(f"- {o['header']}: {o.get('description', '')}" for o in spec["outputs"].values())
        prompt = DIGEST_PROMPT.format(outputs=outs, instruction=spec["instruction"])
        card = _parse_json(_chat([{"role": "user", "content": prompt + "\n\n" + text}])).get("card", "")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(card, encoding="utf-8")
        return card


def _doc_context(job: dict[str, Any], doc_path: str, keys: list[str], cache: dict[str, Any], locks: dict) -> str:
    """Справка для пачки: точное совпадение — фрагментом страницы, серия — сжатой карточкой её страниц."""
    from .. import doc_index

    # Серия: напечатанный префикс кода (5532 для 55.32.9.024.0000) или страница точного совпадения.
    groups: dict[str, dict[str, Any]] = {}
    lines = []
    for k in keys:
        if not k:
            continue
        if k not in cache:
            cache[k] = doc_index.search_document(doc_path, k, max_results=3)
        res = cache[k]
        if not res["results"]:
            lines.append(f"- {k}: not found in the document")
            continue
        top = res["results"][0]["page"]
        series = res["matched"] if res.get("match") == "prefix" else f"p{top}"
        g = groups.setdefault(series, {"pages": tuple(sorted(x["page"] for x in res["results"][:2])), "keys": []})
        g["keys"].append(k)
        lines.append(f"- {k}: {res.get('match', res['mode'])} match ({res.get('matched', '')}), series card {series}")
        if res.get("match") == "exact":
            lines.append(f"  excerpt p.{top}: {res['results'][0]['snippet']}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        cards = dict(zip(groups, pool.map(lambda s: _digest(job, doc_path, s, groups[s]["pages"], locks), groups)))
    parts = ["Lookup results:", *lines]
    used = sum(map(len, parts))
    for series, g in groups.items():
        if used > CONTEXT_CHARS_PER_BATCH:
            parts.append(f"[series card {series} skipped: context limit]")
            continue
        used += len(cards[series])
        parts.append(f"\n=== Series card {series}, pages {list(g['pages'])} ===\n{cards[series]}")
    return "\n".join(parts)


def _run_batch(job: dict[str, Any], batch: list[dict[str, Any]], cache: dict[str, Any], locks: dict) -> list[dict[str, Any]]:
    spec = job["spec"]
    outputs = spec["outputs"]
    out_desc = "\n".join(
        f'- "{col}" = {o["header"]}: {o.get("description", "")}'
        + (f' Allowed values: {json.dumps(o["allowed"], ensure_ascii=False)}' if o.get("allowed") else "")
        for col, o in outputs.items()
    )
    user = [f"Task: {spec['instruction']}", f"Output columns:\n{out_desc}"]
    if spec.get("examples"):
        user.append(
            "Examples already filled by the user: follow their wording, style and choice of values exactly "
            "(decode the technical data yourself, the examples show the format):\n"
            + "\n".join(json.dumps(e, ensure_ascii=False) for e in spec["examples"])
        )
    if spec.get("document"):
        keys = [str(r["lookup"]) for r in batch if r.get("lookup") not in (None, "")]
        user.append(_doc_context(job, spec["document"], keys, cache, locks))
    user.append("Rows:\n" + "\n".join(json.dumps({"row": r["row"], **r["input"]}, ensure_ascii=False) for r in batch))
    answer = _parse_json(_chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(user)}]))
    rows = answer.get("rows", []) if isinstance(answer, dict) else answer
    wanted = {r["row"] for r in batch}
    return [r for r in rows if isinstance(r, dict) and r.get("row") in wanted]


def _save_status(job: dict[str, Any]) -> None:
    f = DATA_ROOT / job["status_file"]
    f.parent.mkdir(parents=True, exist_ok=True)
    public = {k: v for k, v in job.items() if k not in ("spec_rows",)}
    f.write_text(json.dumps(public, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def start_job(
    path: str,
    instruction: str,
    input_columns: list[str],
    outputs: dict[str, dict[str, Any]],
    sheet: str | None = None,
    header_row: int | None = None,
    data_start_row: int | None = None,
    filters: list[dict[str, Any]] | None = None,
    document: str | None = None,
    lookup_column: str | None = None,
    batch_size: int = 30,
    workers: int = 3,
    max_rows: int = 0,
    example_rows: list[int] | None = None,
    skip_filled: bool = True,
    public_base_url: str = "",
) -> dict[str, Any]:
    _llm_config()
    src = resolve_under_data(path)
    grid = _pick_sheet(_load(src), sheet)
    hr = header_row or grid.detect_header()
    in_cols = [_resolve_column(grid, hr, c) for c in input_columns]
    out_cols = {}
    for c, o in outputs.items():
        label = _col_label(grid, hr, _resolve_column(grid, hr, c))
        out_cols[label.split(" ")[0]] = {**(o or {}), "header": label}
    look = _resolve_column(grid, hr, lookup_column) if lookup_column else None
    if document:
        resolve_under_data(document)  # проверить путь сразу, а не в фоне
    conds = [(_resolve_column(grid, hr, f["column"]), f.get("op", "eq"), f.get("value")) for f in filters or []]
    heads = _headers(grid, hr)
    out_idx = {col: column_index_from_string(col) for col in out_cols}
    explicit = set(example_rows or [])
    rows, examples = [], []
    for r in range(max(hr + 1, data_start_row or 0), len(grid.rows) + 1):
        if not any(not _blank(v) for v in grid.rows[r - 1]):
            continue
        if not all(_match(grid.cell(r, c), op, t) for c, op, t in conds):
            continue
        item = {
            "row": r,
            "input": {(heads[c - 1] or _col_label(grid, hr, c)): grid.cell(r, c) for c in in_cols},
            "lookup": grid.cell(r, look) if look else None,
        }
        filled = all(not _blank(grid.cell(r, c)) for c in out_idx.values())
        # Строки, которые человек уже заполнил сам, — образцы стиля, а не работа для задания.
        if r in explicit or (filled and not explicit and len(examples) < MAX_EXAMPLES):
            examples.append({"input": item["input"], "output": {col: grid.cell(r, c) for col, c in out_idx.items()}})
            continue
        if filled and skip_filled:
            continue
        rows.append(item)
    if max_rows:
        rows = rows[:max_rows]
    if not rows:
        raise ValueError("No rows match the filters")
    if len(rows) > MAX_JOB_ROWS:
        raise ValueError(f"Too many rows ({len(rows)}), max {MAX_JOB_ROWS}")
    # Соседние артикулы обычно из одной серии: сортировка даёт пачкам общие страницы каталога.
    if look:
        rows.sort(key=lambda x: str(x["lookup"] or ""))
    batch_size = max(1, min(batch_size, 60))
    job_id = uuid.uuid4().hex[:10]
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    job = {
        "job_id": job_id,
        "state": "running",
        "source": str(src.relative_to(DATA_ROOT)).replace("\\", "/"),
        "sheet": grid.name,
        "rows_total": len(rows),
        "rows_done": 0,
        "rows_filled": 0,
        "batches_total": (len(rows) + batch_size - 1) // batch_size,
        "batches_done": 0,
        "errors": [],
        "low_confidence": [],
        "unresolved": [],
        "output": None,
        "download_url": None,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status_file": f"exports/{day}/jobs/{job_id}.json",
        "spec": {
            "instruction": instruction,
            "outputs": out_cols,
            "document": document,
            "header_row": hr,
            "examples": examples,
        },
        "examples_used": [e["input"] for e in examples],
    }
    with _jobs_lock:
        _jobs[job_id] = job
    batches = [rows[i : i + batch_size] for i in range(0, len(rows), batch_size)]
    threading.Thread(
        target=_worker, args=(job, batches, max(1, min(workers, 6)), public_base_url), name=f"job:{job_id}", daemon=True
    ).start()
    return job_status(job_id)


def _worker(job: dict[str, Any], batches: list[list[dict[str, Any]]], workers: int, base_url: str) -> None:
    write_lock = threading.Lock()
    cache: dict[str, Any] = {}
    locks: dict[Any, threading.Lock] = {}
    allowed = {c: o["allowed"] for c, o in job["spec"]["outputs"].items() if o.get("allowed")}

    def process(batch: list[dict[str, Any]]) -> None:
        try:
            answers = _run_batch(job, batch, cache, locks)
        except Exception as e:
            log.warning("job %s batch failed: %s", job["job_id"], e)
            with write_lock:
                job["errors"].append({"rows": [r["row"] for r in batch], "error": str(e)[:300]})
                job["rows_done"] += len(batch)
                job["batches_done"] += 1
            return
        updates = []
        got = set()
        for a in answers:
            vals = {c: v for c, v in (a.get("values") or {}).items() if c in job["spec"]["outputs"] and not _blank(v)}
            got.add(a["row"])
            if a.get("confidence") == "low" or len(vals) < len(job["spec"]["outputs"]):
                bucket = "unresolved" if not vals else "low_confidence"
                job[bucket].append({"row": a["row"], "note": str(a.get("note", ""))[:200]})
            if vals:
                updates.append({"row": a["row"], "values": vals})
        missing = [r["row"] for r in batch if r["row"] not in got]
        with write_lock:
            for r in missing:
                job["unresolved"].append({"row": r, "note": "model returned no answer for this row"})
            pending.extend(updates)
            job["rows_done"] += len(batch)
            job["batches_done"] += 1
            if time.monotonic() - last_flush[0] > FLUSH_EVERY_SEC:
                flush()
            _save_status(job)

    def flush() -> None:
        # Большой файл грузится и сохраняется секунды, поэтому пишем не каждую пачку, а раз в FLUSH_EVERY_SEC.
        last_flush[0] = time.monotonic()
        if not pending:
            return
        res = fill_table(
            job["output"] or job["source"], list(pending), sheet=job["sheet"],
            header_row=job["spec"]["header_row"], allowed_values=allowed, public_base_url=base_url,
        )
        job["output"], job["download_url"] = res["path"], res["download_url"]
        job["rows_filled"] += len(pending)
        pending.clear()
        for p in res["problems"]:
            job["errors"].append({"rows": [p.get("row")], "error": f"{p.get('column', '')}: {p.get('error')} {p.get('value', '')}"})

    pending: list[dict[str, Any]] = []
    last_flush = [time.monotonic()]
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(process, batches))
        with write_lock:
            flush()
        job["state"] = "done"
    except Exception as e:
        job["state"] = "failed"
        job["errors"].append({"rows": [], "error": f"{type(e).__name__}: {e}"})
    job["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_status(job)


def job_status(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        for f in (DATA_ROOT / "exports").glob(f"*/jobs/{job_id}.json"):
            job = json.loads(f.read_text(encoding="utf-8"))
            break
    if job is None:
        raise FileNotFoundError(f"Job {job_id} not found")
    out = {k: v for k, v in job.items() if k not in ("spec", "errors", "low_confidence", "unresolved")}
    out["errors_total"] = len(job["errors"])
    out["errors"] = job["errors"][:10]
    out["low_confidence_total"] = len(job["low_confidence"])
    out["low_confidence"] = job["low_confidence"][:20]
    out["unresolved_total"] = len(job["unresolved"])
    out["unresolved"] = job["unresolved"][:20]
    out["note"] = (
        "Job is running: tell the user it continues in background and check again later with table_job_status."
        if job["state"] == "running"
        else "Give the user download_url as a markdown link and summarize unresolved / low-confidence rows."
    )
    return out
