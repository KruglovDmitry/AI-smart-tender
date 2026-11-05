"""Lossless text decoding and bounded logical parts with source line locations."""
from pathlib import Path
import codecs

TEXT_EXTENSIONS = {'.txt', '.md', '.json', '.xml', '.log', '.ini', '.yml', '.yaml',
                   '.toml', '.py', '.js', '.ts', '.css', '.html', '.htm', '.sql', '.rst'}
MAX_TEXT_BYTES = 64 * 1024 * 1024
PART_CHARS = 4000

def decode_bytes(raw):
    if len(raw) > MAX_TEXT_BYTES:
        raise ValueError('Text file exceeds 64 MiB')
    for bom, encoding in [(codecs.BOM_UTF32_LE, 'utf-32'), (codecs.BOM_UTF32_BE, 'utf-32'),
                          (codecs.BOM_UTF8, 'utf-8-sig'), (codecs.BOM_UTF16_LE, 'utf-16'),
                          (codecs.BOM_UTF16_BE, 'utf-16')]:
        if raw.startswith(bom):
            return raw.decode(encoding), encoding, []
    # BOM-less UTF-16 is common in Windows exports; require a strong NUL pattern.
    sample = raw[:65536]
    if sample and sample.count(b'\x00') > len(sample) // 5:
        odd, even = sample[1::2].count(0), sample[::2].count(0)
        encoding = 'utf-16-le' if odd > even else 'utf-16-be'
        try:
            return raw.decode(encoding), encoding, ['encoding_inferred_without_bom']
        except UnicodeDecodeError:
            raise ValueError('Binary or malformed UTF-16 file; text decoding refused')
    try:
        return raw.decode('utf-8'), 'utf-8', []
    except UnicodeDecodeError:
        pass
    for encoding in ('cp1251', 'cp866', 'latin-1'):
        try:
            return raw.decode(encoding), encoding, ['encoding_inferred: confirm Cyrillic text if needed']
        except UnicodeDecodeError:
            continue
    raise ValueError('Cannot decode text')

def read_text(path):
    if path.stat().st_size > MAX_TEXT_BYTES:
        raise ValueError('Text file exceeds 64 MiB')
    return decode_bytes(path.read_bytes())

def extract_parts(path):
    text, encoding, warnings = read_text(path)
    parts = []
    line = 1
    for offset in range(0, max(1, len(text)), PART_CHARS):
        chunk = text[offset:offset + PART_CHARS]
        end_line = line + chunk.count('\n')
        parts.append({'text': chunk, 'chars': len(chunk), 'broken': False, 'empty': not chunk.strip(),
                      'images': 0, 'image_refs': [], 'unit': 'logical_part', 'warnings': warnings,
                      'blocks': [{'id': f'text-{offset}', 'kind': 'source_text', 'encoding': encoding,
                                  'char_from': offset, 'char_to': offset + len(chunk),
                                  'line_from': line, 'line_to': end_line}]})
        line = end_line
    return parts

def extract_text(path):
    # Preserve whitespace, leading zeros, spelling and source code exactly after decoding.
    return read_text(path)[0]
