"""Optional physical-page rendering using an explicitly available LibreOffice."""
from __future__ import annotations
import os
import shutil
import subprocess
from pathlib import Path


def render_pdf(idx):
    from ..office import convert
    return convert(idx.path, idx.cache, '.pdf', 'TOOLS_DOCX_SOFFICE')



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
