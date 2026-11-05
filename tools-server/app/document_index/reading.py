"""Bounded PDF reading for tool callers; no changes to chat upload processing."""
from __future__ import annotations

import json
from typing import Any

from ..document_tool.pdf import vision

SMALL_MAX_PAGES = 8
SMALL_MAX_CHARS = 40000
LAYOUT_BUDGET = 6000


def read_pdf(path: str, mode: str = "auto", pages: str | None = None,
             start_page: int = 1, text_offset: int = 0, max_pages: int = 8,
             max_chars: int = 40000, include_tables: bool = True,
             include_layout: bool = True, visual_mode: str = "auto",
             max_visual_pages: int = 2) -> dict[str, Any]:
    from . import get_index, document_info, parse_pages

    if mode not in {"auto", "full", "index"} or visual_mode not in {"off", "auto", "required"}:
        raise ValueError("Invalid reading or visual mode")
    if not 1 <= max_pages <= 10 or not 1 <= max_chars <= 120000 or not 0 <= max_visual_pages <= 10:
        raise ValueError("Invalid PDF reading budget")
    idx = get_index(path)
    if not idx.is_pdf:
        raise ValueError("read_pdf requires a PDF; use the native reader for other formats")
    count = len(idx.pages)
    if not 1 <= start_page <= count or text_offset < 0:
        raise ValueError("Invalid cursor")
    small = count <= SMALL_MAX_PAGES and sum(p['chars'] for p in idx.pages) <= SMALL_MAX_CHARS
    selected = "index" if mode == "index" or (mode == "auto" and not pages and not small and start_page == 1 and text_offset == 0) else "read"
    if selected == "index":
        result = document_info(path, start_page, min(count, start_page + 39))
        result.update(mode="index", content_complete=False, pages_read=[],
                      next_outline_page=start_page + 40 if start_page + 40 <= count else None,
                      note="Index only: no full reading. Search for specific facts, or use mode=full and follow next_cursor for exhaustive reading.")
        return result
    nums = parse_pages(pages, count) if pages else list(range(start_page, count + 1))
    if pages and (start_page != 1 or text_offset):
        if start_page not in nums:
            raise ValueError("Cursor page must be in the requested pages")
        nums = nums[nums.index(start_page):]
    if not nums:
        raise ValueError("No pages requested")
    output, completed, failed, warnings = [], [], [], []
    remaining, layout_remaining, visual_calls = max_chars, LAYOUT_BUDGET, 0
    next_cursor = None
    for pos, n in enumerate(nums):
        if len(output) >= max_pages or remaining <= 0:
            next_cursor = {"start_page": n, "text_offset": 0}
            break
        page = idx.read_page(n, include_tables=include_tables,
            include_structure=include_layout or visual_mode != 'off')
        item = {"page": n, "warnings": list(page["warnings"]), "source": page["source"]}
        text, source, structure = page['text'], page['source'], page['structure']
        tables = structure.get('tables', []) if include_tables else []
        content = text
        if tables:
            content += '\n\n[TABLES: extracted from page, verify headers and units]\n' + '\n\n'.join(t['markdown'] for t in tables)
        offset = text_offset if pos == 0 else 0
        if offset > len(content):
            raise ValueError("text_offset exceeds page content; restart this page with offset=0")
        chunk = content[offset:offset + remaining]
        item.update(text=chunk, content_offset=offset, content_chars=len(content))
        remaining -= len(chunk)
        partial = offset + len(chunk) < len(content)
        if include_layout and structure:
            layout = {k: v for k, v in structure.items() if k not in {'warnings', 'visual_recommended'}}
            layout['tables'] = [{k: v for k, v in table.items() if k != 'markdown'} for table in tables]
            size = len(json.dumps(layout, ensure_ascii=False))
            if size <= layout_remaining:
                item['layout'] = layout
                layout_remaining -= size
            else:
                item['warnings'].append('layout_omitted_budget: read this page separately')
        visual_needed = visual_mode == 'required' or (visual_mode == 'auto' and
            (structure.get('visual_recommended', False) or source != 'text'))
        item['visual_recommended'] = visual_needed
        if visual_needed:
            if remaining <= 0:
                item["warnings"].append("visual_answer_budget_exhausted: use view_page for this page")
            elif visual_calls < max_visual_pages:
                visual_calls += 1
                try:
                    result = vision.view_page(path, n,
                        'Опиши структуру и извлеки данные схем, изображений и сложных таблиц. '
                        'Сохрани заголовки, единицы и связи строк с колонками; укажи неразборчивое.',
                        detail='auto')
                    answer = result['answer']
                    item['visual'] = {k: v for k, v in result.items() if k != 'answer'}
                    item['visual']['answer'] = answer[:remaining]
                    if len(answer) > remaining:
                        item['warnings'].append('visual_answer_truncated: use view_page for this page')
                    remaining -= len(item['visual']['answer'])
                except Exception as exc:
                    item['warnings'].append(f"visual_unavailable: {type(exc).__name__}; configure TOOLS_VL_* and retry view_page")
            else:
                item['warnings'].append('visual_budget_exhausted: use view_page for this page')
        item['complete'] = not partial and not item['warnings']
        output.append(item)
        if item['complete']:
            completed.append(n)
        else:
            failed.append(n)
        if partial:
            next_cursor = {"start_page": n, "text_offset": offset + len(chunk)}
            break
    returned = [p['page'] for p in output]
    processed = len(returned)
    unread = nums[processed:]
    if next_cursor and returned and next_cursor['start_page'] == returned[-1]:
        unread = [returned[-1]] + unread
    scope_done = next_cursor is None
    return {"path": idx.rel, "mode": "read", "page_count": count, "pages": output,
            "pages_read": returned, "pages_complete": completed, "pages_needing_review": failed,
            "requested_scope_complete": scope_done and not failed,
            "content_complete": scope_done and not failed and returned == list(range(1, count + 1)) and text_offset == 0,
            "truncated": next_cursor is not None,
            "next_cursor": next_cursor,
            "remaining_pages": unread[:100], "remaining_page_count": len(unread),
            "continuation": "Repeat mode=full with next_cursor; preserve explicit pages scope if supplied. Completion describes this response, not prior calls.",
            "visual_attempts": visual_calls,
            "budgets": {"content_chars": max_chars, "layout_chars": LAYOUT_BUDGET},
            "warnings": warnings}
