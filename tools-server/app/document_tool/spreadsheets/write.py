"""Save, reopen and compare before atomically publishing an XLSX."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from datetime import date, datetime
from openpyxl import load_workbook
from openpyxl.xml.functions import tostring
from openpyxl.utils import column_index_from_string


def _value(v):
    if isinstance(v,date) and not isinstance(v,datetime):return datetime.combine(v,datetime.min.time())
    return v


def snapshot(wb):
    out={}
    for ws in wb.worksheets:
        cells={}
        for cell in ws._cells.values():
            if cell.value is None and not cell.has_style and cell.comment is None and not cell.hyperlink:continue
            cells[cell.coordinate]=(_value(cell.value),cell.data_type,tuple(cell._style) if cell.has_style else None,
                (cell.comment.text,cell.comment.author) if cell.comment else None,
                cell.hyperlink.target if cell.hyperlink else None)
        out[ws.title]={'cells':cells,'merged':sorted(str(x) for x in ws.merged_cells.ranges),
            'validations':tostring(ws.data_validations.to_tree()),'filter':tostring(ws.auto_filter.to_tree()),
            'freeze':ws.freeze_panes,'state':ws.sheet_state,
            'columns':{k:(v.width,v.hidden,v.min or column_index_from_string(k),v.max or column_index_from_string(k)) for k,v in ws.column_dimensions.items()},
            'rows':{k:(v.height,v.hidden) for k,v in ws.row_dimensions.items()},
            'tables':{v.name:tostring(v.to_tree()) for v in ws.tables.values()},
            'charts':len(ws._charts),'images':len(ws._images)}
    return {'sheets':out,'order':wb.sheetnames,'names':{k:tostring(v.to_tree()) for k,v in wb.defined_names.items()}}


def save_verified(wb,dest: Path):
    expected=snapshot(wb)
    formulas=sum(c[1]=='f' for sh in expected['sheets'].values() for c in sh['cells'].values())
    fd,tmp=tempfile.mkstemp(prefix='.table-',suffix='.xlsx',dir=dest.parent);os.close(fd)
    try:
        wb.save(tmp)
        check=load_workbook(tmp,data_only=False)
        try:
            actual=snapshot(check)
            if expected!=actual:raise ValueError('Saved workbook failed verification; previous output preserved')
        finally:check.close()
        os.replace(tmp,dest)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {'reopened':True,'cells_checked':sum(len(s['cells']) for s in expected['sheets'].values()),
            'formulas':formulas,'recalculated':False,
            'warnings':['formulas_not_recalculated; saved_caches_may_be_missing_after_edit'] if formulas else []}
