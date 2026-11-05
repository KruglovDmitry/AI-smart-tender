"""Optional isolated LibreOffice conversion; never modifies the source."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

def executable(env_key='TOOLS_OFFICE_SOFFICE'):
    result = os.getenv(env_key) or os.getenv('TOOLS_OFFICE_SOFFICE') or os.getenv('TOOLS_DOCX_SOFFICE') or shutil.which('soffice')
    if not result:
        for folder in (r'C:\Program Files', r'C:\Program Files (x86)'):
            candidate = Path(folder) / 'LibreOffice/program/soffice.exe'
            if candidate.is_file():
                result = str(candidate)
                break
    if not result:
        raise ValueError('Office conversion requires LibreOffice: set TOOLS_OFFICE_SOFFICE (or TOOLS_DOCX_SOFFICE). Source remains unchanged.')
    return result

def convert(path, cache, suffix='.pdf', env_key='TOOLS_OFFICE_SOFFICE'):
    from ..document_index import _lock_for
    if suffix not in {'.pdf', '.pptx'}:
        raise ValueError('Unsupported office conversion target')
    dest = cache / 'render' / ('document' + suffix)
    with _lock_for(str(dest)):
        if dest.is_file():
            return dest
        program = executable(env_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='office-render-', dir=dest.parent) as temporary:
            work = Path(temporary)
            from .sniff import detect_extension
            source = work / ('document' + detect_extension(path))
            shutil.copy2(path, source)
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            try:
                result = subprocess.run([program, '-env:UserInstallation=' + (work / 'profile').as_uri(),
                    '--headless', '--nologo', '--norestore', '--convert-to', suffix[1:], '--outdir', str(work), str(source)],
                    capture_output=True, timeout=120, creationflags=flags)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ValueError('Office conversion could not finish; source remains unchanged') from exc
            output = work / ('document' + suffix)
            if result.returncode or not output.is_file():
                raise ValueError('Office conversion failed; source remains unchanged')
            if suffix == '.pdf':
                from .pdf.read import extract_pages
                extract_pages(output)
            else:
                from pptx import Presentation
                Presentation(output)
            os.replace(output, dest)
    return dest
