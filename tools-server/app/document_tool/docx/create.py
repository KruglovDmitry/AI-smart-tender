"""Create DOCX, reopen and verify its body and media before publishing."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from lxml import etree as ET
from docx import Document
from docx.shared import Inches
from .. import resolve_under_data
from ..artifacts import export_dest,make_download_url


def _snapshot(doc):
    return {'body':ET.tostring(doc.element.body,method='c14n'),
            'media':{str(p.partname):hashlib.sha256(p.blob).hexdigest()
                     for p in doc.part.package.parts if p.content_type.startswith('image/')}}


def create_docx(filename:str,blocks:list[dict],title:str='',public_base_url:str=''):
    if not blocks or len(blocks)>10000:raise ValueError('Supply between 1 and 10000 blocks')
    doc=Document()
    if title:doc.add_heading(title,0)
    total=0
    for spec in blocks:
        kind=spec.get('kind','paragraph');text=str(spec.get('text',''))
        total+=len(text)
        if total>2_000_000:raise ValueError('Document content exceeds 2 million characters')
        if kind=='heading':
            level=int(spec.get('level',1))
            if not 1<=level<=9:raise ValueError('Heading level must be 1..9')
            doc.add_heading(text,level)
        elif kind in {'paragraph','bullet','numbered'}:
            p=doc.add_paragraph(style={'bullet':'List Bullet','numbered':'List Number'}.get(kind,'Normal'))
            run=p.add_run(text);run.bold=bool(spec.get('bold',False));run.italic=bool(spec.get('italic',False))
        elif kind=='table':
            rows=spec.get('rows') or []
            width=max((len(r) for r in rows),default=0)
            if not rows or len(rows)>5000 or not 1<=width<=64:raise ValueError('Invalid table dimensions')
            table=doc.add_table(rows=len(rows),cols=width);table.style='Table Grid'
            for target_row,row in zip(table.rows,rows):
                for cell,value in zip(target_row.cells,row):
                    cell_text='' if value is None else str(value);total+=len(cell_text)
                    if total>2_000_000:raise ValueError('Document content exceeds 2 million characters')
                    cell.text=cell_text
            if spec.get('header',True):
                from docx.oxml import OxmlElement
                table.rows[0]._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
        elif kind=='image':
            path=resolve_under_data(str(spec.get('image_path','')))
            if not path.is_file() or path.suffix.lower() not in {'.png','.jpg','.jpeg'}:raise ValueError('Use a local PNG/JPEG image')
            if path.stat().st_size>20*1024*1024:raise ValueError('Image exceeds 20 MiB')
            width=float(spec.get('width_inches',5))
            if not .1<=width<=6.5:raise ValueError('Image width must be 0.1..6.5 inches')
            doc.add_picture(str(path),width=Inches(width))
            if text:doc.add_paragraph(text,style='Caption')
        elif kind=='page_break':doc.add_page_break()
        else:raise ValueError('Unknown DOCX block: '+kind)
    expected=_snapshot(doc)
    dest=export_dest(filename,'.docx')
    fd,tmp=tempfile.mkstemp(prefix='.docx-',suffix='.docx',dir=dest.parent);os.close(fd)
    try:
        doc.save(tmp)
        reopened=Document(tmp)
        if _snapshot(reopened)!=expected:raise ValueError('DOCX verification failed; output not published')
        os.replace(tmp,dest)
    finally:Path(tmp).unlink(missing_ok=True)
    rel=dest.relative_to(resolve_under_data('')).as_posix()
    return {'path':rel,'filename':dest.name,'download_url':make_download_url(rel,public_base_url),
            'verification':{'package_reopened':True,'body_and_media_checked':True,'layout_rendered':False},
            'warnings':['Physical pagination/layout requires a render check; not performed during creation.']}
