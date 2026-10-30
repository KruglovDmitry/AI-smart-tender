"""Import the official Rosstat DOCX tables without executing document content."""
from pathlib import Path
import argparse, hashlib, json, re, zipfile
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-dir', required=True, type=Path, help='Directory with the two DOCX files extracted from the Rosstat archive')
parser.add_argument('--archive', required=True, type=Path, help='Original official OKPD2.rar for its SHA-256')
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--version', required=True)
parser.add_argument('--retrieved-at', required=True, help='YYYY-MM-DD')
args = parser.parse_args()
ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
entries = {}
for path in sorted(args.source_dir.glob('*.docx')):
    xml = ET.fromstring(zipfile.ZipFile(path).read('word/document.xml'))
    for row in xml.findall('.//w:tr', ns):
        cells = [' '.join(' '.join(t.text or '' for t in c.findall('.//w:t', ns)).split())
                 for c in row.findall('w:tc', ns)]
        if len(cells) >= 2 and re.fullmatch(r'\d{2}(?:\.\d{1,3}){0,3}', cells[0]):
            code = cells[0]
            paragraphs = [' '.join(' '.join(t.text or '' for t in p.findall('.//w:t', ns)).split())
                          for p in row.findall('w:tc', ns)[1].findall('w:p', ns)]
            title = next((p for p in paragraphs if p), cells[1])
            if code in entries and entries[code]['title'] != title:
                raise ValueError(f'Conflicting entry {code}: {entries[code]["title"]} / {title}')
            record = {'code': code, 'title': title, 'description': cells[1]}
            if len(cells[1]) >= len(entries.get(code, {}).get('description', '')):
                entries[code] = record
assert len(entries) > 10000 and entries['27.12.24.190']['title'] == 'Реле прочие'
snapshot = {'system': 'okpd2', 'version': args.version, 'retrieved_at': args.retrieved_at,
            'url': 'https://rosstat.gov.ru/storage/mediabank/OKPD2.rar',
            'index_url': 'https://rosstat.gov.ru/classification',
            'archive_sha256': hashlib.sha256(args.archive.read_bytes()).hexdigest(),
            'entries': list(entries.values())}
dest=args.output
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
print(len(entries), dest.stat().st_size)
