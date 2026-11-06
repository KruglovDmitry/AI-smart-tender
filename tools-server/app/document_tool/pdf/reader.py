"""PDF page extraction shared by tools and upload adapters.

The owner supplies a cache; PDF decisions live here, navigation does not.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import read, ocr


class PdfReader:
    def __init__(self, path: Path, pages: list[dict], cached: Callable):
        self.path, self.pages, self.cached = path, pages, cached

    def record(self, n: int) -> dict:
        if not 1 <= n <= len(self.pages):
            raise ValueError(f"Page {n} out of range 1..{len(self.pages)}")
        return self.pages[n - 1]

    def needs_ocr(self, n: int) -> bool:
        p = self.record(n)
        return p['broken'] or (p['empty'] and p['images'] > 0)

    def text(self, n: int, run_ocr: bool = True) -> tuple[str, str]:
        p = self.record(n)
        if not self.needs_ocr(n):
            return p['text'], 'text'
        value = self.cached('ocr', n)
        if value is not None:
            return value, 'ocr'
        if not run_ocr:
            return '' if p['broken'] else p['text'], 'ocr_pending'
        return self.cached('ocr', n, lambda: ocr.page_text(self.path, n)), 'ocr'

    def structure(self, n: int, include_tables: bool = True) -> dict:
        self.record(n)
        def produce():
            result = read.page_structure(self.path, n, include_tables)
            result['tables_included'] = include_tables
            return result
        return self.cached('structure', n, produce,
            accept=lambda data: not include_tables or data.get('tables_included', False),
            cache_if=lambda data: not any(w.startswith('table_detection_failed') for w in data['warnings']))

    def page(self, n: int, *, run_ocr: bool = True, include_tables: bool = True,
             include_structure: bool = True, tables_on_ocr: bool = True) -> dict[str, Any]:
        self.record(n)
        warnings = []
        try:
            text, source = self.text(n, run_ocr)
        except Exception as exc:
            text, source = '', 'unavailable'
            warnings.append(f'text_extraction_failed: {type(exc).__name__}')
        structure = {}
        table_enabled = include_tables and (tables_on_ocr or source == 'text')
        if include_structure or table_enabled:
            try:
                structure = self.structure(n, table_enabled)
                warnings.extend(structure['warnings'])
            except Exception as exc:
                warnings.append(f'layout_failed: {type(exc).__name__}')
        tables = structure.get('tables', []) if table_enabled else []
        if table_enabled and source == "text" and "outside_text" in structure:
            # Trusted table cells are omitted here; callers append the grid separately.
            # OCR stays intact: the text layer of a scan is not the page prose.
            text = structure["outside_text"]
        # Do not mutate a cached full structure when a caller requests text only.
        if structure and not include_tables:
            structure = {**structure, 'tables': []}
        return {'page': n, 'text': text, 'source': source, 'structure': structure,
                'tables': [t['markdown'] for t in tables], 'warnings': warnings}
