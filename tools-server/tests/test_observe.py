"""Журнал вызовов: файл сервиса, без секретов и без текста документа."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    from app import config

    importlib.reload(config)
    import app.document_tool as documents
    import app.document_index as index
    import app.main as main

    for module in (documents, index, main):
        importlib.reload(module)
    return TestClient(main.app), tmp_path


def test_summary_keeps_warning_codes_and_hides_prose():
    from app.observe import summarize

    fields = summarize({
        "content_complete": False,
        "warnings": ["table_grid_ambiguous: table-1; verify with view_page"],
        "pages": [{"warnings": ["OCR may misread labels, numbers and layout"]}],
        "content": "секретный текст страницы",
    })
    assert fields["content_complete"] is False
    assert fields["warnings"] == "table_grid_ambiguous"
    assert fields["chars"] == len("секретный текст страницы")
    assert "секретный" not in str(fields)


def test_log_file_records_call_and_redacts_secret(client):
    c, root = client
    assert c.get("/health").status_code == 200
    (root / "note.txt").write_text("hello", encoding="utf-8")
    assert c.post("/read_text", json={"path": "note.txt"}).status_code == 200
    missing = c.post("/read_text", json={"path": "missing.txt"})
    assert missing.status_code == 404

    from app.observe import get_logger

    get_logger().info("authorization: Bearer secret-token")
    text = (root / "_logs" / "tools.log").read_text(encoding="utf-8")
    assert "POST /read_text" in text
    assert "note.txt" in text
    assert "GET /health" not in text
    assert "missing.txt" in text and " 404 " in text
    assert "secret-token" not in text and "authorization=***" in text
    assert "hello" not in text
