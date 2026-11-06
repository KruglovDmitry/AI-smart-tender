from pathlib import Path
import re
import zipfile

def _doc_text_ole(path: Path) -> str:
    """Текст из Word 97–2003 (.doc) без внешних программ: таблица кусков (piece table) из потока
    WordDocument и потока 0Table/1Table. Нужен в desktop-версии под Windows, где нет antiword/catdoc."""
    import struct

    import olefile

    with olefile.OleFileIO(str(path)) as ole:
        if ole.get_size('WordDocument') > 64 * 1024 * 1024:
            raise ValueError('WordDocument stream exceeds 64 MiB')
        word = ole.openstream("WordDocument").read()
        if len(word) < 426:
            raise ValueError('Truncated Word FIB')
        flags = struct.unpack_from("<H", word, 0x0A)[0]
        if flags & 0x8100:
            raise ValueError('Encrypted legacy Word document is unsupported')
        table_name = "1Table" if flags & 0x0200 else "0Table"
        if not ole.exists(table_name):
            raise ValueError(f"нет потока {table_name}")
        if ole.get_size(table_name) > 64 * 1024 * 1024:
            raise ValueError('Word piece-table stream exceeds 64 MiB')
        table = ole.openstream(table_name).read()
    fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x01A2)
    clx = table[fc_clx:fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:  # Prc — форматирование, пропускаем
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos >= len(clx) or clx[pos] != 0x02:
        raise ValueError("не найдена таблица кусков текста")
    size = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5:pos + 5 + size]
    count = (size - 4) // 12
    cps = struct.unpack_from(f"<{count + 1}I", plc, 0)
    parts: list[str] = []
    for i in range(count):
        fc = struct.unpack_from("<I", plc, (count + 1) * 4 + i * 8 + 2)[0]
        length = cps[i + 1] - cps[i]
        if fc & 0x40000000:  # 8-битный текст (cp1252)
            start = (fc & ~0x40000000) // 2
            parts.append(word[start:start + length].decode("cp1252", errors="replace"))
        else:
            parts.append(word[fc:fc + 2 * length].decode("utf-16-le", errors="replace"))
    text = "".join(parts)
    # Служебные символы Word: конец абзаца/ячейки → перенос строки, поля и объекты — убрать.
    text = text.replace(chr(13), chr(10)).replace(chr(7), chr(9)).replace(chr(11), chr(10)).replace(chr(12), chr(10))
    text = "".join(ch for ch in text if ch in (chr(10), chr(9)) or ch >= " ")
    return text


def _extract_legacy_doc(path: Path) -> str:
    """Extract text from legacy .doc via antiword/catdoc (installed in image), else a built-in reader."""
    import shutil
    import subprocess

    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('Legacy Word file exceeds 128 MiB')
    errors: list[str] = []
    for cmd in (
        ["antiword", "-m", "UTF-8.txt", str(path)],
        ["antiword", str(path)],
        ["catdoc", "-d", "utf-8", str(path)],
        ["catdoc", str(path)],
    ):
        binary = cmd[0]
        if not shutil.which(binary):
            errors.append(f"{binary} not installed")
            continue
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=60,
            )
            if proc.returncode == 0 and (proc.stdout or b"").strip():
                text = proc.stdout.decode("utf-8", errors="replace")
                if not text.strip():
                    text = proc.stdout.decode("cp1251", errors="replace")
                return text
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:200]
            errors.append(f"{' '.join(cmd)} -> rc={proc.returncode} {err}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{binary}: {e}")

    try:
        text = _doc_text_ole(path)
        if text.strip():
            return text
        errors.append("built-in reader: empty text")
    except Exception as e:  # noqa: BLE001
        errors.append(f"built-in reader: {e}")
    raise ValueError(
        "Cannot read legacy .doc ("
        + "; ".join(errors[:4])
        + "). Convert to .docx or install antiword/catdoc."
    )


def _extract_rtf(path: Path) -> str:
    """RTF 7-битный: кириллица идёт escape-последовательностями \\'xx в кодовой странице из \\ansicpgNNNN."""
    from striprtf.striprtf import rtf_to_text

    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('RTF exceeds 64 MiB')
    raw = path.read_bytes().decode("latin-1")
    m = re.search(r"\\ansicpg(\d+)", raw[:4096])
    return rtf_to_text(raw, encoding=f"cp{m.group(1)}" if m else "cp1251", errors="replace")


def _extract_opendocument(path: Path) -> str:
    """OpenDocument (LibreOffice): текст из content.xml — абзацы и заголовки строками, ячейки таблиц через « | »."""
    from lxml import etree as ET

    ns_text = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    ns_table = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    with zipfile.ZipFile(path) as zf:
        if sum(i.file_size for i in zf.infolist()) > 256 * 1024 * 1024:
            raise ValueError('OpenDocument expanded package exceeds 256 MiB')
        if zf.getinfo('content.xml').file_size > 64 * 1024 * 1024:
            raise ValueError('OpenDocument XML exceeds 64 MiB')
        raw = zf.read('content.xml')
        root = ET.fromstring(raw, ET.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, remove_comments=True, remove_pis=True))
        if root.getroottree().docinfo.doctype:
            raise ValueError('OpenDocument DTDs are unsupported')
    lines: list[str] = []
    emitted_chars = 0
    omitted = False

    def emit(value, repeat=1):
        nonlocal emitted_chars
        emitted_chars += (len(value) + 1) * repeat
        if emitted_chars > 8_000_000:
            raise ValueError('OpenDocument expanded text exceeds budget')
        lines.extend([value] * repeat)

    def text_of(el) -> str:
        parts = [el.text or ""]
        for ch in el:
            tag = ch.tag.split("}")[-1]
            if tag == "s":
                count = int(ch.get(f"{{{ns_text}}}c", "1"))
                if not 0 <= count <= 10000: raise ValueError('OpenDocument space repetition exceeds budget')
                parts.append(" " * count)
            elif tag in ("tab", "line-break"):
                parts.append(" ")
            else:
                parts.append(text_of(ch))
            parts.append(ch.tail or "")
        return "".join(parts)

    def walk(el) -> None:
        nonlocal omitted
        for ch in el:
            if ch.tag == f"{{{ns_table}}}table-row":
                cells = []
                for cell in ch:
                    if not cell.tag.endswith(('}table-cell', '}covered-table-cell')): continue
                    repeat = int(cell.get(f'{{{ns_table}}}number-columns-repeated', '1'))
                    if repeat < 1: raise ValueError('OpenDocument column repetition exceeds budget')
                    value = text_of(cell).strip() or cell.get('{urn:oasis:names:tc:opendocument:xmlns:office:1.0}string-value', '') or cell.get('{urn:oasis:names:tc:opendocument:xmlns:office:1.0}value', '')
                    # Spreadsheets pad the used range with empty repeats up to the whole grid.
                    if not value:
                        if repeat > 1:
                            omitted = True
                        else:
                            cells.append('')
                        continue
                    if repeat > 10000: raise ValueError('OpenDocument column repetition exceeds budget')
                    cells.extend([value] * repeat)
                if any(cells):
                    repeat = int(ch.get(f'{{{ns_table}}}number-rows-repeated', '1'))
                    if repeat < 1: raise ValueError('OpenDocument row repetition exceeds budget')
                    if repeat > 10000:
                        omitted = True
                        repeat = 1
                    row = ' | '.join(cells)
                    emit(row, repeat)
            elif ch.tag in (f"{{{ns_text}}}p", f"{{{ns_text}}}h"):
                t = text_of(ch).strip()
                if t:
                    emit(t)
            else:
                walk(ch)

    walk(root)
    if omitted:
        lines.append('[OpenDocument: empty repeated rows or columns were omitted]')
    return "\n".join(lines)
