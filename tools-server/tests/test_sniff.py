"""Тип файла по содержимому: файлы с площадок бывают без расширения или с ложным расширением."""

from __future__ import annotations

import os
import zipfile

from docx import Document  # python-docx ставится вместе с markitdown[docx]

from app.document_tool.extract import extract_file
from app.document_tool.sniff import detect_extension, sniff_path


def _docx(path, text: str, padding: int = 0):
    doc = Document()
    doc.add_paragraph(text)
    doc.save(path)
    if padding:
        # Несжимаемый балласт: оглавление zip уезжает дальше первого мегабайта.
        with zipfile.ZipFile(path, "a", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("word/media/blob.bin", os.urandom(padding))


def test_big_docx_without_extension_is_docx(tmp_path):
    f = tmp_path / "I. Общие сведения (товар)"
    _docx(f, "Срок поставки 30 дней", padding=2 << 20)
    assert f.stat().st_size > 1 << 20
    assert sniff_path(f) == ".docx"
    out = extract_file(f)
    assert out["extension"] == ".docx" and "Срок поставки 30 дней" in out["content"]


def test_zip_named_docx_is_read_as_archive(tmp_path):
    inner = tmp_path / "spec.docx"
    _docx(inner, "Спецификация: кабель ВВГнг 3x2.5")
    fake = tmp_path / "Скачать все документы.docx"
    with zipfile.ZipFile(fake, "w") as zf:
        zf.write(inner, "spec.docx")
    assert detect_extension(fake) == ".zip"
    assert "кабель ВВГнг" in extract_file(fake)["content"]


def test_known_extension_kept_when_content_agrees(tmp_path):
    f = tmp_path / "ТЗ.docx"
    _docx(f, "текст")
    assert detect_extension(f) == ".docx"
    txt = tmp_path / "notes.txt"
    txt.write_text("просто текст", encoding="utf-8")
    assert detect_extension(txt) == ".txt"
