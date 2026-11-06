from __future__ import annotations
import hashlib
import io
import os
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ARCHIVE_EXTENSIONS = {'.zip', '.rar', '.7z'}
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 10000

def _safe_name(name):
    name = name.replace('\\', '/')
    parts = PurePosixPath(name).parts
    return bool(name) and not name.startswith('/') and not any(p in {'..', '.'} or ':' in p for p in parts) and '\x00' not in name

def _zip_name(info):
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode('cp437').decode('cp866')
    except UnicodeError:
        return info.filename

def _type(path):
    from ..sniff import detect_extension
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('Archive exceeds 512 MiB')
    kind = detect_extension(path)
    if kind not in ARCHIVE_EXTENSIONS:
        raise ValueError('ZIP, RAR or 7z archive required')
    return kind

def _rar_module():
    import rarfile
    import shutil
    if os.name == 'nt' and not shutil.which(rarfile.BSDTAR_TOOL):
        candidate = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/tar.exe'
        if candidate.is_file():
            rarfile.BSDTAR_TOOL = str(candidate)
    return rarfile


def _entries(path):
    kind = _type(path)
    if kind == '.zip':
        try:
            archive_cm = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise ValueError('Archive is not a readable ZIP') from exc
        with archive_cm as archive:
            rows = [{'name': _zip_name(i), 'stored_name': i.filename, 'size_bytes': i.file_size,
                     'directory': i.is_dir(), 'link': stat.S_ISLNK(i.external_attr >> 16),
                     'encrypted': bool(i.flag_bits & 1)} for i in archive.infolist()]
    elif kind == '.rar':
        try:
            rarfile = _rar_module()
        except ImportError as exc:
            raise ValueError('RAR support requires the rarfile package and an available unrar/bsdtar backend') from exc
        with rarfile.RarFile(path) as archive:
            rows = [{'name': i.filename, 'stored_name': i.filename, 'size_bytes': i.file_size,
                     'directory': i.is_dir(), 'link': i.is_symlink() or bool(i.file_redir),
                     'encrypted': i.needs_password()} for i in archive.infolist()]
    else:
        try:
            import py7zr
        except ImportError as exc:
            raise ValueError('7z support requires the py7zr package') from exc
        with py7zr.SevenZipFile(path) as archive:
            encrypted = archive.needs_password()
            rows = [{'name': i.filename, 'stored_name': i.filename, 'size_bytes': i.uncompressed or 0,
                     'directory': i.is_directory, 'link': bool(getattr(i, 'is_symlink', False)),
                     'encrypted': encrypted} for i in archive.list()]
    if len(rows) > MAX_ENTRIES:
        raise ValueError('Archive exceeds 10000 entries')
    from collections import Counter
    counts = Counter(row['name'] for row in rows)
    for row in rows:
        name = row['name']
        reason = ('unsafe_member_name' if not _safe_name(name) else 'link_not_followed' if row['link'] else
                  'encrypted_member' if row['encrypted'] else 'member_exceeds_64_MiB' if row['size_bytes'] > MAX_MEMBER_BYTES else
                  'duplicate_member_name' if counts[name] > 1 else None)
        row['blocked_reason'] = reason
    return rows

def list_archive(path, offset=0, limit=100):
    from .. import resolve_under_data
    source = resolve_under_data(path)
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError('Invalid archive pagination')
    rows = _entries(source)
    end = offset + limit
    return {'path': str(source.relative_to(resolve_under_data(''))).replace('\\', '/'),
            'total_entries': len(rows), 'entries': [{k: v for k, v in r.items() if k != 'stored_name'} for r in rows[offset:end]],
            'next_offset': end if end < len(rows) else None,
            'note': 'Manifest only. No documents or nested archives have been read. Open a selected member, then use its format tools.'}

def member_bytes(path, row):
    if row['blocked_reason'] or row['directory']:
        raise ValueError(row['blocked_reason'] or 'Cannot open an archive directory')
    kind = _type(path)
    name = row['stored_name']
    if kind == '.zip':
        with zipfile.ZipFile(path) as archive, archive.open(name) as stream:
            data = stream.read(MAX_MEMBER_BYTES + 1)
    elif kind == '.rar':
        rarfile = _rar_module()
        try:
            with rarfile.RarFile(path) as archive, archive.open(name) as stream:
                data = stream.read(MAX_MEMBER_BYTES + 1)
        except rarfile.Error as exc:
            raise ValueError('Cannot read RAR member; check encryption, file integrity and unrar/bsdtar availability') from exc
    else:
        import py7zr
        from py7zr.io import Py7zIO, WriterFactory
        class Buffer(Py7zIO):
            def __init__(self): self.stream = io.BytesIO()
            def write(self, data):
                if self.stream.tell() + len(data) > MAX_MEMBER_BYTES:
                    raise ValueError('7z member exceeds memory budget')
                return self.stream.write(data)
            def read(self, size=None): return self.stream.read(-1 if size is None else size)
            def seek(self, offset, whence=0): return self.stream.seek(offset, whence)
            def flush(self): pass
            def size(self): return self.stream.getbuffer().nbytes
        class Factory(WriterFactory):
            def __init__(self): self.product = None
            def create(self, filename):
                if filename != name or self.product is not None:
                    raise ValueError('Unexpected 7z member; selective extraction refused')
                self.product = Buffer()
                return self.product
        factory = Factory()
        with py7zr.SevenZipFile(path) as archive:
            if sum(i.uncompressed or 0 for i in archive.list()) > MAX_ARCHIVE_BYTES:
                raise ValueError('Solid 7z expanded size exceeds 512 MiB')
            archive.extract(targets=[name], factory=factory)
        if factory.product is None:
            raise ValueError('7z member has no readable stream')
        data = factory.product.stream.getvalue()
    if len(data) > MAX_MEMBER_BYTES:
        raise ValueError('Archive member exceeds 64 MiB')
    return data

def open_member(path, member):
    from .. import resolve_under_data
    from ..sniff import ensure_extension, detect_extension
    source = resolve_under_data(path)
    matches = [r for r in _entries(source) if r['name'] == member]
    if len(matches) != 1:
        raise ValueError('Archive member missing or ambiguous; select a unique manifest name')
    data = member_bytes(source, matches[0])
    # Never turn a member name into a filesystem path. Only content hashes form paths.
    digest = hashlib.sha256(data).hexdigest()
    folder = resolve_under_data('.archive-members/' + digest)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / 'document.bin'
    fd, tmp = tempfile.mkstemp(dir=folder)
    try:
        with os.fdopen(fd, 'wb') as stream: stream.write(data)
        ext = detect_extension(Path(tmp), ensure_extension(member, data))
        if ext and (len(ext) > 10 or not ext[1:].isalnum()): ext = '.bin'
        dest = folder / ('document' + (ext or '.bin'))
        os.replace(tmp, dest)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {'archive_path': path, 'member': member, 'path': dest.relative_to(resolve_under_data('')).as_posix(),
            'size_bytes': len(data), 'note': 'Member saved as a separate copy; source archive is unchanged. Read the returned path with format tools.'}

def extract_parts(path):
    rows = _entries(path)
    parts = []
    for start in range(0, max(1, len(rows)), 25):
        batch = rows[start:start + 25]
        text = '\n'.join(r['name'] + ' | ' + str(r['size_bytes']) + ' bytes' + (' | BLOCKED: ' + r['blocked_reason'] if r['blocked_reason'] else '') for r in batch)
        parts.append({'text': text, 'chars': len(text), 'broken': False, 'empty': not text,
            'images': 0, 'image_refs': [], 'unit': 'archive_manifest_part',
            'blocks': [{'kind': 'archive_member', **{k: v for k, v in r.items() if k != 'stored_name'}} for r in batch],
            'warnings': ['archive_manifest_only: member contents have not been read']})
    return parts

def extract_archive(path, depth=0, budget=None):
    # Legacy read_document compatibility, bounded globally across nested archives.
    from ..extract import extract_file
    from ..sniff import detect_extension
    budget = budget if budget is not None else {'files': 200, 'bytes': 128 * 1024 * 1024, 'chars': 120000}
    rows = _entries(path)
    parts, metadata = [], []
    with tempfile.TemporaryDirectory(prefix='archive-read-') as temporary:
        for row in rows:
            name = row['name']
            if row['directory']: continue
            reason = row['blocked_reason']
            if budget['files'] <= 0 or budget['chars'] <= 0 or row['size_bytes'] > budget['bytes']:
                metadata.append({'filename': name, 'skipped': True, 'reason': 'aggregate_archive_budget_exhausted'})
                break
            if reason:
                metadata.append({'filename': name, 'skipped': True, 'reason': reason})
                continue
            try:
                budget['files'] -= 1
                data = member_bytes(path, row)
                budget['bytes'] -= len(data)
                work = Path(temporary) / 'member.bin'
                work.write_bytes(data)
                ext = detect_extension(work, name)
                work = work.rename(work.with_suffix(ext if ext and len(ext) < 12 and ext[1:].isalnum() else '.bin'))
                if ext in ARCHIVE_EXTENSIONS:
                    if depth >= 2: raise ValueError('archive nested too deep; open it explicitly')
                    inner = extract_archive(work, depth + 1, budget)
                    extracted = inner['content']
                    metadata.extend({**m, 'filename': name + '/' + m['filename']} for m in inner['zip_files'])
                    # Inner calls have already charged the shared character budget.
                else:
                    extracted = extract_file(work)['content']
                    original_length = len(extracted)
                    extracted = extracted[:budget['chars']]
                    budget['chars'] -= len(extracted)
                    metadata.append({'filename': name, 'skipped': False, 'chars': len(extracted), 'truncated': original_length > len(extracted)})
                parts.append('===== ' + name + ' =====\n' + extracted)
                work.unlink(missing_ok=True)
            except Exception as exc:
                metadata.append({'filename': name, 'skipped': True, 'reason': str(exc)[:200]})
    content = '\n\n'.join(parts)
    return {'path': str(path), 'filename': path.name, 'extension': _type(path), 'chars': len(content),
            'content': content, 'zip_files': metadata,
            'truncated': any(m.get('skipped') or m.get('truncated') for m in metadata)}
