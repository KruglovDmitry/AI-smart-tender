"""Постраничный индекс документа для больших файлов (каталоги, тендерная документация).

Текст страниц строится сразу (каталог на 840 страниц — около 10 с) и кэшируется в DATA_ROOT/.index.
Таблицы (≈2 с на страницу) и OCR (≈10 с на страницу) считаются только для нужных страниц и тоже кэшируются.
Модель видит не весь документ, а оглавление, результаты поиска и выбранные страницы.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import re
import threading
from pathlib import Path
from typing import Any

from ..config import DATA_ROOT
from ..document_tool import resolve_under_data
from ..document_tool.pdf import ocr, read as pdf

log = logging.getLogger(__name__)

INDEX_VERSION = 6
INDEX_ROOT = DATA_ROOT / ".index"
# Не-PDF документы режем на условные страницы такого размера, чтобы поиск и чтение работали одинаково.
PSEUDO_PAGE_CHARS = 4000

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_ocr_threads: dict[str, threading.Thread] = {}


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _rel(path: Path) -> str:
    return str(path.relative_to(DATA_ROOT)).replace("\\", "/")


def _cache_dir(rel: str) -> Path:
    return INDEX_ROOT / hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]


class DocIndex:
    def __init__(self, path: Path, cache: Path, meta: dict[str, Any], pages: list[dict[str, Any]], rel: str | None = None):
        self.path = path
        self.rel = rel if rel is not None else _rel(path)
        self.cache = cache
        self.meta = meta
        self.pages = pages
        self._pdf_reader = None
        if self.is_pdf:
            from ..document_tool.pdf.reader import PdfReader
            self._pdf_reader = PdfReader(path, pages, self.cached)

    @property
    def is_pdf(self) -> bool:
        return self.meta.get("file_type", self.path.suffix.lower()) == ".pdf"

    @property
    def is_docx(self) -> bool:
        return self.meta.get("file_type", self.path.suffix.lower()) == ".docx"

    @property
    def page_unit(self) -> str:
        if self.is_pdf: return 'pdf_page'
        return self.pages[0].get('unit', 'logical_part') if self.pages else 'logical_part'

    def page(self, n: int) -> dict[str, Any]:
        if not 1 <= n <= len(self.pages):
            raise ValueError(f"Page {n} out of range 1..{len(self.pages)}")
        return self.pages[n - 1]

    def cached(self, category: str, page: int, producer=None, *, accept=None, cache_if=None):
        """One atomic page cache for all adapters. Categories are internal, not API input."""
        self.page(page)
        f = self.cache / category / f"{page}.json"
        with _lock_for(str(f)):
            if f.exists():
                try:
                    value = json.loads(f.read_text(encoding="utf-8"))
                    if accept is None or accept(value):
                        return value
                except (ValueError, OSError):
                    pass  # rebuild an interrupted or corrupt cache entry
            if producer is None:
                return None
            value = producer()
            if cache_if is None or cache_if(value):
                f.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=f.parent, suffix=".json")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        json.dump(value, stream, ensure_ascii=False)
                    os.replace(tmp, f)
                finally:
                    Path(tmp).unlink(missing_ok=True)
            return value

    def needs_ocr(self, n: int) -> bool:
        return bool(self._pdf_reader and self._pdf_reader.needs_ocr(n))

    def ocr_file(self, n: int) -> Path:
        return self.cache / "ocr" / f"{n}.json"

    def text(self, n: int, run_ocr: bool = True) -> tuple[str, str]:
        if self._pdf_reader:
            return self._pdf_reader.text(n, run_ocr)
        return self.page(n)["text"], "text"

    def read_page(self, n: int, *, run_ocr: bool = True, include_tables: bool = True,
                  include_structure: bool = True, tables_on_ocr: bool = True) -> dict:
        if self._pdf_reader:
            return self._pdf_reader.page(n, run_ocr=run_ocr, include_tables=include_tables,
                include_structure=include_structure, tables_on_ocr=tables_on_ocr)
        text, source = self.text(n, run_ocr)
        record = self.page(n)
        structure = {}
        if include_structure:
            structure = {"unit": self.page_unit, "blocks": record.get("blocks", []),
                         "images": record.get("image_refs", []), "tables": [],
                         "visual_recommended": bool(record.get("image_refs")) or any("visual_review" in w or "rendered_page_review" in w for w in record.get("warnings", []))}
        return {"page": n, "text": text, "source": source, "tables": [], "structure": structure,
                "warnings": record.get("warnings", [])}

    def tables(self, n: int) -> list[str]:
        return self.read_page(n, run_ocr=False, include_structure=False)["tables"]

    def ocr_pending(self) -> list[int]:
        return [i for i in range(1, len(self.pages) + 1) if self.needs_ocr(i) and not self.ocr_file(i).exists()]


# Эти форматы MarkItDown переводит в Markdown с заголовками и таблицами; остальное — прежним extract_file.
MARKITDOWN_EXTENSIONS = {'.msg', '.epub'}


def _split_pseudo_pages(text: str) -> list[str]:
    """Условные страницы ~PSEUDO_PAGE_CHARS, разрез по заголовку или пустой строке, а не посреди таблицы."""
    pages, cur = [], []
    size = 0
    for line in text.splitlines(keepends=True):
        boundary = line.startswith("#") or not line.strip()
        if size >= PSEUDO_PAGE_CHARS and boundary and not line.lstrip().startswith("|"):
            pages.append("".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line)
        if size >= PSEUDO_PAGE_CHARS * 2:  # длинный абзац или таблица без пустых строк
            pages.append("".join(cur))
            cur, size = [], 0
    if cur:
        pages.append("".join(cur))
    return pages or [""]


def _build_pages(path: Path, *, archive_manifest: bool = True, markup_source: bool = True) -> tuple[list[dict[str, Any]], str]:
    from ..document_tool.sniff import detect_extension

    ext = detect_extension(path)
    work = path
    tmp_dir = None
    if path.suffix.lower() != ext and ext in (MARKITDOWN_EXTENSIONS | {".pdf"}):
        import tempfile

        tmp_dir = Path(tempfile.mkdtemp(prefix="pages-"))
        work = tmp_dir / f"file{ext}"
        work.write_bytes(path.read_bytes())
    try:
        if ext == ".pdf":
            return pdf.extract_pages(work), pdf.BACKEND
        if ext == ".docx":
            from ..document_tool.docx.read import extract_parts
            return extract_parts(work), "docx-ooxml"
        if ext in {'.pptx', '.ppt'}:
            from ..document_tool.presentations.read import extract_parts
            return extract_parts(work), 'python-pptx'
        from ..document_tool.raster import IMAGE_EXTENSIONS
        if ext in IMAGE_EXTENSIONS:
            from ..document_tool.images.read import extract_parts
            return extract_parts(work), 'pillow-metadata'
        from ..document_tool.text.read import TEXT_EXTENSIONS, extract_parts
        if ext in TEXT_EXTENSIONS and (markup_source or ext not in {'.html', '.htm', '.xml'}):
            return extract_parts(work), 'text-lines'
        from ..document_tool.archives.read import ARCHIVE_EXTENSIONS
        if ext in ARCHIVE_EXTENSIONS:
            if archive_manifest:
                from ..document_tool.archives.read import extract_parts
                return extract_parts(work), 'archive-manifest'
            from ..document_tool.archives.read import extract_archive
            extracted = extract_archive(work)
            records = [pdf.page_record(c, images=0) for c in _split_pseudo_pages(extracted['content'])]
            if extracted['truncated']:
                for record in records:
                    record['warnings'] = ['archive_contents_incomplete: some members skipped or truncated']
            return records, 'archive-contents'
        text, backend = "", ""
        if ext in MARKITDOWN_EXTENSIONS or (not markup_source and ext in {'.html', '.htm', '.xml'}):
            try:
                from markitdown import MarkItDown

                text, backend = MarkItDown(enable_plugins=False).convert(str(work)).text_content or "", "markitdown"
            except Exception as e:
                log.warning("MarkItDown failed for %s: %s; falling back", path.name, e)
        if not backend:
            from ..document_tool.extract import extract_file

            text, backend = extract_file(path)["content"], "extract_file"
        records = [pdf.page_record(c, images=0) for c in _split_pseudo_pages(text)]
        if ext in {'.doc', '.rtf', '.odt', '.ods', '.odp'}:
            for record in records:
                record['warnings'] = ['text_only_legacy_extraction: layout, fields and embedded images are not fully represented']
        return records, backend
    finally:
        if tmp_dir is not None:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)


def get_index(path_str: str) -> DocIndex:
    path = resolve_under_data(path_str)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path_str}")
    rel = _rel(path)
    cache = _cache_dir(rel)
    st = path.stat()
    from ..document_tool.sniff import detect_extension

    stamp = {"version": INDEX_VERSION, "path": rel, "size": st.st_size, "mtime": st.st_mtime_ns,
             "file_type": detect_extension(path)}
    with _lock_for(rel):
        meta_f, pages_f = cache / "meta.json", cache / "pages.json"
        if meta_f.exists() and pages_f.exists():
            meta = json.loads(meta_f.read_text(encoding="utf-8"))
            if all(meta.get(k) == v for k, v in stamp.items()):
                return DocIndex(path, cache, meta, json.loads(pages_f.read_text(encoding="utf-8")))
        if cache.exists():
            import shutil

            shutil.rmtree(cache, ignore_errors=True)
        cache.mkdir(parents=True, exist_ok=True)
        pages, backend = _build_pages(path)
        meta = {**stamp, "backend": backend, "page_count": len(pages)}
        pages_f.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
        meta_f.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        log.info("Indexed %s: %d pages (%s)", rel, len(pages), backend)
        idx = DocIndex(path, cache, meta, pages)
    _start_background_ocr(idx)
    return idx


def _start_background_ocr(idx: DocIndex) -> None:
    """Битые страницы распознаём в фоне, чтобы поиск со временем находил и их."""
    if not idx.ocr_pending() or not ocr.available():
        return
    t = _ocr_threads.get(idx.rel)
    if t and t.is_alive():
        return

    def run() -> None:
        for n in idx.ocr_pending():
            try:
                idx.text(n, run_ocr=True)
            except Exception as e:  # одна плохая страница не должна останавливать остальные
                log.warning("OCR failed %s p.%d: %s", idx.rel, n, e)

    t = threading.Thread(target=run, name=f"ocr:{idx.rel}", daemon=True)
    _ocr_threads[idx.rel] = t
    t.start()


# ---------- оглавление ----------


def _title(text: str) -> str:
    for line in text.splitlines():
        # Разметка Markdown (заголовки, ячейки таблиц, жирный) в оглавлении только мешает.
        s = re.sub(r"\s{2,}", " ", re.sub(r"[#|*]+", " ", line)).strip()
        if len(s) >= 4 and not s.isdigit() and not re.fullmatch(r"[\W\d_]+", s):
            return s[:90]
    return ""


def document_info(path: str, page_from: int = 1, page_to: int = 0) -> dict[str, Any]:
    idx = get_index(path)
    n = len(idx.pages)
    page_to = min(page_to or n, n)
    rows = []
    for i in range(max(1, page_from), page_to + 1):
        text, src = idx.text(i, run_ocr=False)
        p = idx.page(i)
        row = {"page": i, "title": _title(text), "chars": len(text)}
        if p["images"]:
            row["images"] = p["images"]
        if src != "text":
            row["source"] = src
        if not idx.is_pdf:
            row["blocks"] = len(p.get("blocks", []))
            row["image_ids"] = [r["id"] for r in p.get("image_refs", [])][:10]
        rows.append(row)
    return {
        "path": idx.rel,
        "pages": n,
        "chars": sum(p["chars"] for p in idx.pages),
        "broken_pages": [i for i, p in enumerate(idx.pages, 1) if p["broken"]],
        "image_only_pages": [i for i, p in enumerate(idx.pages, 1) if p["empty"] and p["images"]],
        "ocr_pending": len(idx.ocr_pending()),
        "outline": rows,
        "page_unit": idx.page_unit,
        **({"skill_hint": "Load document_skill(file_type=pdf) once for PDF reading instructions."} if idx.is_pdf else {"skill_hint": "Load document_skill(file_type=docx) once for DOCX reading/creation instructions."} if idx.is_docx else {}),
        "note": "Use search_document then read_pages/read_file. Check page_unit: logical parts, slides, frames and archive manifests are different units.",
    }


# ---------- поиск ----------

_SEP = re.compile(r"[\s.\-/_]+")


def normalize_code(s: str) -> str:
    """55.32.9.024.0000, 55 32 9 024 0000 и 553290240000 дают один ключ."""
    return re.sub(r"[^0-9A-ZА-ЯЁ]", "", (s or "").upper())


def _is_code(q: str) -> bool:
    return bool(re.search(r"\d", q)) and len(normalize_code(q)) >= 3 and len(q.split()) <= 2


def _code_prefixes(q: str) -> list[str]:
    """Варианты от полного к короткому: по группам (55.32.9.024 → 55.32.9 → 55.32), без разделителей — по символу."""
    groups = [g for g in _SEP.split(q.strip()) if g]
    if len(groups) > 1:
        out = [normalize_code("".join(groups[:k])) for k in range(len(groups), 1, -1)]
    else:
        key = normalize_code(q)
        out = [key[:k] for k in range(len(key), 3, -1)]
    seen: list[str] = []
    for k in out:
        if k and k not in seen:
            seen.append(k)
    return seen


def _snippet(text: str, pos: int, radius: int = 220) -> str:
    a, b = max(0, pos - radius), min(len(text), pos + radius)
    return ("…" if a else "") + text[a:b].strip() + ("…" if b < len(text) else "")


def _norm_positions(text: str) -> tuple[str, list[int]]:
    """Текст, где точки, дефисы и слэши внутри кода выброшены, а пробелы и переводы строк стали границей «|»;
    плюс карта позиций обратно в исходный текст (для сниппетов)."""
    chars, pos = [], []
    for i, ch in enumerate(text.upper()):
        if ch.isalnum():
            chars.append(ch)
            pos.append(i)
        elif ch not in ".-/_" and chars and chars[-1] != "|":
            chars.append("|")
            pos.append(i)
    return "".join(chars), pos


def _search_code(idx: DocIndex, q: str, max_results: int) -> dict[str, Any]:
    texts = [idx.text(i, run_ocr=False)[0] for i in range(1, len(idx.pages) + 1)]
    normed = [_norm_positions(t) for t in texts]
    for level, key in enumerate(_code_prefixes(q)):
        hits = []
        for i, (nt, pos) in enumerate(normed):
            if key not in nt:
                continue
            # Код должен начинаться с начала токена; полный код ещё и заканчиваться вместе с ним.
            good, whole = [], []
            for m in re.finditer(re.escape(key), nt):
                s, e = m.start(), m.end()
                if s and nt[s - 1] != "|":
                    continue
                ends_token = e == len(nt) or nt[e] == "|"
                if level == 0 and not ends_token:
                    continue
                good.append(s)
                if ends_token:
                    whole.append(s)
            if not good:
                continue
            # При совпадении по префиксу выше страницы, где префикс стоит отдельно («Тип 55.32»): это описание серии.
            first = (whole or good)[0]
            hits.append((len(whole), len(good), i + 1, _snippet(texts[i], pos[first])))
        if hits:
            hits.sort(key=lambda h: (-h[0], -h[1], h[2]))
            hits = [(c, p, s) for _, c, p, s in hits]
            return {
                "mode": "code",
                "match": "exact" if level == 0 else "prefix",
                "matched": key,
                "total_pages": len(hits),
                "results": [{"page": p, "occurrences": c, "snippet": s} for c, p, s in hits[:max_results]],
            }
    return {"mode": "code", "match": "none", "total_pages": 0, "results": []}


def _stem(w: str) -> str:
    w = w.lower().replace("ё", "е")
    return w[:6] if len(w) > 6 else w


def _search_text(idx: DocIndex, q: str, max_results: int) -> dict[str, Any]:
    terms = [_stem(w) for w in re.findall(r"[\wА-Яа-яЁё]{2,}", q)]
    if not terms:
        return {"mode": "text", "total_pages": 0, "results": []}
    hits = []
    for i in range(1, len(idx.pages) + 1):
        text = idx.text(i, run_ocr=False)[0]
        low = text.lower().replace("ё", "е")
        found = {t: low.find(t) for t in terms}
        matched = [t for t, p in found.items() if p >= 0]
        if not matched:
            continue
        score = len(matched) * 100 + sum(low.count(t) for t in matched)
        first = min(found[t] for t in matched)
        hits.append((score, i, len(matched), _snippet(text, first)))
    hits.sort(key=lambda h: (-h[0], h[1]))
    return {
        "mode": "text",
        "terms": terms,
        "total_pages": len(hits),
        "results": [
            {"page": p, "terms_matched": m, "snippet": s} for _, p, m, s in hits[:max_results]
        ],
    }


def search_document(path: str, query: str, max_results: int = 10, mode: str = "auto") -> dict[str, Any]:
    idx = get_index(path)
    q = (query or "").strip()
    if not q:
        raise ValueError("Empty query")
    use_code = mode == "code" or (mode == "auto" and _is_code(q))
    res = _search_code(idx, q, max_results) if use_code else _search_text(idx, q, max_results)
    if use_code and res["match"] == "none" and mode == "auto":
        res = _search_text(idx, q, max_results)
    pending = len(idx.ocr_pending())
    return {
        "path": idx.rel,
        "query": q,
        "page_unit": idx.page_unit,
        **res,
        **({"ocr_pending_pages": pending} if pending else {}),
        "note": (
            "match=prefix: the full code is not printed in the document; catalogs often build codes from a "
            "series prefix plus option tables — read those pages and decode the remaining digits."
            if res.get("match") == "prefix"
            else "Use read_pages to read the pages found."
        ),
    }


# ---------- чтение страниц ----------


def parse_pages(spec: str, page_count: int) -> list[int]:
    out: list[int] = []
    for part in re.split(r"[,\s;]+", (spec or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out.extend(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            out.append(int(part))
        else:
            raise ValueError(f"Bad page spec: {part!r} (use e.g. '5', '5-7', '5,9,12')")
    bad = [p for p in out if not 1 <= p <= page_count]
    if bad:
        raise ValueError(f"Pages out of range 1..{page_count}: {bad[:5]}")
    return list(dict.fromkeys(out))


def read_pages(
    path: str, pages: str, include_tables: bool = True, max_chars: int = 60000, max_pages: int = 10
) -> dict[str, Any]:
    idx = get_index(path)
    nums = parse_pages(pages, len(idx.pages))
    if len(nums) > max_pages:
        raise ValueError(f"Too many pages ({len(nums)}); request at most {max_pages} per call")
    out, used, truncated = [], 0, False
    for n in nums:
        page = idx.read_page(n, include_tables=include_tables, include_structure=False, tables_on_ocr=False)
        item: dict[str, Any] = {"page": n, "source": page["source"], "text": page["text"]}
        if page["tables"]:
            item["tables"] = page["tables"]
        if page["warnings"]:
            item["warnings"] = page["warnings"]
        size = len(item["text"]) + sum(len(t) for t in item.get("tables", []))
        if used + size > max_chars:
            item["text"] = item["text"][: max(0, max_chars - used)] + "\n[... truncated ...]"
            item.pop("tables", None)
            truncated = True
            out.append(item)
            break
        used += size
        out.append(item)
    return {"path": idx.rel, "page_count": len(idx.pages), "page_unit": idx.page_unit, "pages": out, "truncated": truncated}
