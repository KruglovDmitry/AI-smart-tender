"""Shared table readers. Identifiers stay strings; formulas are never evaluated here."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

HEADER_SCAN_ROWS = 15


def value(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


class Sheet:
    def __init__(self, name, rows, formulas=None, raw=None, mode='values', warnings=None):
        self.name, self.rows = name, rows
        self.width = max((len(r) for r in rows), default=0)
        self.formulas, self.raw = formulas or {}, raw or {}
        self.formula_rows = {r for r,c in self.formulas}
        self.mode, self.warnings = mode, warnings or []

    def cell(self, r, c):
        if self.mode == 'formulas' and (r,c) in self.formulas:
            return self.formulas[r,c]
        row = self.rows[r-1] if 0 < r <= len(self.rows) else []
        return row[c-1] if 0 < c <= len(row) else None

    def occupied(self, r, c):
        return (r,c) in self.formulas or not blank(self.cell(r,c))

    def row_has_data(self, r):
        return any(not blank(v) for v in self.rows[r-1]) or r in self.formula_rows

    def detect_header(self):
        best, best_score = 1, -1.0
        for r in range(1,min(len(self.rows),HEADER_SCAN_ROWS)+1):
            vals=[v for v in self.rows[r-1] if not blank(v)]
            if not vals: continue
            texts=sum(isinstance(v,str) and not re.fullmatch(r'[\d\s.,-]+',v) for v in vals)
            score=len(vals)/max(1,self.width)+texts/len(vals)
            if score > best_score + .25:best,best_score=r,score
        return best


def load(path: Path, read_mode='values') -> dict[str, Sheet]:
    if read_mode not in {'values','formulas','both'}:
        raise ValueError('read_mode must be values, formulas or both')
    from ..sniff import detect_extension
    ext=detect_extension(path)
    if ext in {'.xlsx','.xlsm'}:
        from openpyxl import load_workbook
        # File objects allow files with absent/false suffixes to use the same reader.
        with path.open('rb') as fh, path.open('rb') as ch:
            wb=load_workbook(fh,read_only=True,data_only=False)
            cached=load_workbook(ch,read_only=True,data_only=True)
            try:
                result={}
                for ws in wb.worksheets:
                    rows=[];formulas={};raw={}
                    import itertools
                    for r,(cells,vals) in enumerate(itertools.zip_longest(ws.iter_rows(),cached[ws.title].iter_rows(values_only=True),fillvalue=()),1):
                        row=[]
                        for c,cell in enumerate(cells,1):
                            val=vals[c-1] if c<=len(vals) else None
                            if cell.data_type=='f':
                                if not isinstance(cell.value,str):
                                    raise ValueError('Array/data-table formulas require a dedicated reader')
                                formulas[r,c]=cell.value
                            if isinstance(val,(int,float)) and not isinstance(val,bool) and val>=0 and float(val).is_integer() and re.fullmatch('0{2,}',cell.number_format or ''):
                                raw[r,c]=value(val); val=str(int(val)).zfill(len(cell.number_format))
                            row.append(value(val))
                        rows.append(row)
                    warnings=[]
                    if formulas:warnings.append('formula_results_are_saved_cache_not_recalculated')
                    result[ws.title]=Sheet(ws.title,rows,formulas,raw,read_mode,warnings)
                return result
            finally:wb.close();cached.close()
    if ext in {'.csv','.tsv'}:
        data=path.read_bytes()
        if data.startswith((b'\xff\xfe',b'\xfe\xff')):encoding='utf-16'
        else:
            try:data.decode('utf-8-sig');encoding='utf-8-sig'
            except UnicodeDecodeError:encoding='cp1251'
        text=data.decode(encoding)
        try:dialect=csv.Sniffer().sniff(text[:65536],delimiters=',;\t|');delimiter=dialect.delimiter
        except csv.Error:
            # Multiline quoted fields can confuse Sniffer's row consistency check.
            try:delimiter=csv.Sniffer().sniff(text.splitlines()[0],delimiters=',;\t|').delimiter
            except (csv.Error,IndexError):delimiter='\t' if ext=='.tsv' else ','
        rows=list(csv.reader(io.StringIO(text,newline=''),delimiter=delimiter,strict=True))
        return {'csv':Sheet('csv',rows,mode=read_mode,warnings=[f'csv_encoding={encoding}; delimiter={delimiter!r}; all_fields_are_strings'])}
    if ext=='.xls':
        if read_mode!='values':raise ValueError('Legacy XLS formulas are unavailable; convert to XLSX to inspect formulas')
        import pandas as pd
        frames=pd.read_excel(path,sheet_name=None,header=None,dtype=object,keep_default_na=False,engine='xlrd')
        return {str(n):Sheet(str(n),[[value(v) for v in row] for row in df.itertuples(index=False,name=None)],warnings=['legacy_xls_formula_metadata_unavailable']) for n,df in frames.items()}
    raise ValueError(f'Not a supported table: {ext}')


def extract_text(path: Path) -> str:
    from openpyxl.utils import get_column_letter
    parts=[]
    from ..sniff import detect_extension
    for sh in load(path,'both' if detect_extension(path)!='.xls' else 'values').values():
        parts.append(f'Sheet: {sh.name}; rows: {len(sh.rows)}; columns: {sh.width}')
        parts.extend('Warning: '+w for w in sh.warnings)
        for r,row in enumerate(sh.rows,1):
            cells=[]
            for c,v in enumerate(row,1):
                if (r,c) in sh.formulas:
                    cells.append(f'{get_column_letter(c)}={sh.formulas[r,c]} [saved_value={v!r}]')
                else:cells.append('' if v is None else str(v))
            parts.append(f'{r}: '+' | '.join(cells))
    return '\n'.join(parts)
