"""Shared naming and download routing for generated document artifacts."""
from __future__ import annotations
import re
import uuid
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote
from . import resolve_under_data

ALLOWED_DOWNLOADS={'.xlsx','.xls','.csv','.docx','.pdf'}


def export_dest(filename:str,suffix:str='.xlsx') -> Path:
    if suffix not in ALLOWED_DOWNLOADS:raise ValueError('Unsupported export format')
    name=(filename or 'export').replace('\\','/').split('/')[-1]
    stem=re.sub(r'[^\w.\-а-яА-ЯёЁ]+','_',Path(name).stem).strip('._')[:80] or 'export'
    folder=resolve_under_data('exports/'+datetime.now(timezone.utc).strftime('%Y-%m-%d'))
    folder.mkdir(parents=True,exist_ok=True)
    dest=folder/(stem+suffix)
    while dest.exists():dest=folder/(stem+'_'+uuid.uuid4().hex[:12]+suffix)
    return dest


def make_download_url(rel:str,public_base_url:str='') -> str:
    return public_base_url.rstrip('/')+'/download_file?path='+quote(rel)


def resolve_export_file(relative_path:str) -> Path:
    path=resolve_under_data(relative_path)
    if not path.is_file():raise FileNotFoundError('Export not found')
    try:path.relative_to(resolve_under_data('exports'))
    except ValueError as exc:raise PermissionError('Only exports can be downloaded') from exc
    if path.suffix.lower() not in ALLOWED_DOWNLOADS:raise PermissionError('Unsupported download format')
    return path
