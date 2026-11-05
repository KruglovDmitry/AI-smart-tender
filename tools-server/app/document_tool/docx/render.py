"""Optional physical-page rendering using an explicitly available LibreOffice."""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def render_pdf(idx):
    from ...document_index import _lock_for
    executable=os.getenv('TOOLS_DOCX_SOFFICE') or shutil.which('soffice')
    if not executable:
        for p in [Path(r'C:\Program Files\LibreOffice\program\soffice.exe'),Path(r'C:\Program Files (x86)\LibreOffice\program\soffice.exe')]:
            if p.is_file():executable=str(p);break
    if not executable:raise ValueError('Physical DOCX pages require LibreOffice: set TOOLS_DOCX_SOFFICE. Use embedded image IDs without a renderer.')
    dest=idx.cache/'render'/'document.pdf'
    with _lock_for(str(dest)):
        if dest.is_file():return dest
        dest.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='docx-render-',dir=dest.parent) as temporary:
            work=Path(temporary);source=work/'document.docx';shutil.copy2(idx.path,source)
            profile=work/'profile'
            # Separate profile, no shared Word/LibreOffice session or visible windows.
            flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
            result=subprocess.run([executable,'-env:UserInstallation='+profile.as_uri(),'--headless','--nologo','--norestore',
                '--convert-to','pdf','--outdir',str(work),str(source)],capture_output=True,timeout=120,creationflags=flags)
            output=work/'document.pdf'
            if result.returncode or not output.is_file():raise ValueError('DOCX render failed; source remains unchanged')
            from ..pdf.read import extract_pages
            extract_pages(output)  # Validate PDF before caching.
            os.replace(output,dest)
    return dest



def render_document(path):
    from ...document_index import get_index
    from .. import resolve_under_data
    idx=get_index(path)
    if not idx.is_docx:raise ValueError('A DOCX file is required')
    pdf=render_pdf(idx)
    rel=pdf.relative_to(resolve_under_data('')).as_posix()
    rendered=get_index(rel)
    return {'path':idx.rel,'rendered_path':rel,'physical_pages':len(rendered.pages),
            'page_unit':'rendered_physical_page','note':'Pagination depends on LibreOffice and installed fonts.'}
