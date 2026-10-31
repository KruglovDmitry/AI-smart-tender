"""Прогон всего корпуса: разбирается ли файл нашим загрузчиком и встроенным Open WebUI, сколько текста, сколько
страниц требуют OCR, сколько времени. OCR и таблицы здесь выключены (их стоимость меряем на эталонном наборе).

python corpus_scan.py  ->  out/corpus_scan.jsonl
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from common import CORPUS_ROOTS, OUT, sha1, sniff, write_jsonl

SKIP_EXT = {".json", ".gitkeep", ".log", ".png", ".jpg", ".sqlite3"}


def scan_one(path_str: str) -> dict:
    import extractors
    from app.doc_index import pdf

    path = Path(path_str)
    real = sniff(path)
    row = {"path": path_str, "name": path.name, "ext": path.suffix.lower(), "real": real, "size": path.stat().st_size}
    for kind, fn in (("ours", lambda: extractors.ours(path, ocr=False, tables=False)), ("builtin", lambda: extractors.builtin(path))):
        t = time.perf_counter()
        try:
            docs = fn()
            chars = sum(len(d["text"]) for d in docs)
            row[kind] = {"ok": chars > 50, "docs": len(docs), "chars": chars,
                         "backend": (docs[0]["meta"].get("extraction") if docs else None)}
        except Exception as e:
            row[kind] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        row[kind]["sec"] = round(time.perf_counter() - t, 2)
    if real == ".pdf":
        try:
            pages = pdf.extract_pages(path)
            row["pdf_pages"] = len(pages)
            row["pdf_broken"] = sum(p["broken"] for p in pages)
            row["pdf_scanned"] = sum(p["empty"] and p["images"] > 0 for p in pages)
        except Exception as e:
            row["pdf_error"] = str(e)[:200]
    return row


def corpus_files() -> list[Path]:
    seen, out = set(), []
    for root in CORPUS_ROOTS:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file() or "_agent" in p.parts or p.suffix.lower() in SKIP_EXT or p.stat().st_size == 0:
                continue
            h = sha1(p)
            if h in seen:
                continue
            seen.add(h)
            out.append(p)
    return out


def main() -> None:
    files = corpus_files()
    print(f"{len(files)} unique files")
    rows = []
    with ProcessPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(scan_one, str(p)): p for p in files}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                rows.append(f.result(timeout=600))
            except Exception as e:
                rows.append({"path": str(futs[f]), "crash": str(e)[:200]})
            if i % 25 == 0:
                print(f"  {i}/{len(files)}")
    write_jsonl(OUT / "corpus_scan.jsonl", rows)
    print("done")


if __name__ == "__main__":
    main()
