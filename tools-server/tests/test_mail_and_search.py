"""Почта и веб-поиск: настройки дашборда, разбор писем и выдача DuckDuckGo."""

from __future__ import annotations

import importlib

import pytest


def _modules(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    for name in (
        "MAIL_IMAP_HOST", "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_SMTP_HOST", "SEARCH_API_KEY", "SEARCH_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    from app import config, model_config

    importlib.reload(config)
    importlib.reload(model_config)
    from app import mail_tool
    from app.web_tool import search as web_search

    return importlib.reload(mail_tool), importlib.reload(web_search)


def test_mail_requires_dashboard_settings(tmp_path, monkeypatch):
    mail, _search = _modules(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="не настроена"):
        mail.list_messages()


def test_send_rejects_a_bad_address_before_connecting(tmp_path, monkeypatch):
    mail, _search = _modules(tmp_path, monkeypatch)
    monkeypatch.setenv("MAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("MAIL_USERNAME", "owner@example.com")
    monkeypatch.setenv("MAIL_PASSWORD", "secret")
    with pytest.raises(ValueError, match="Некорректный адрес"):
        mail.send_message(["не адрес"], "Тема", "Текст")


def test_duckduckgo_results_are_parsed_without_network(tmp_path, monkeypatch):
    _mail, search = _modules(tmp_path, monkeypatch)
    html = """
    <div class="result">
      <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs">Example docs</a>
      <a class="result__snippet">Краткое описание</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://other.example/page">Другая страница</a>
      <td class="result__snippet">Ещё текст</td>
    </div>
    """
    results = search.parse_duckduckgo(html, limit=5)
    assert results == [
        {"title": "Example docs", "url": "https://example.com/docs", "snippet": "Краткое описание"},
        {"title": "Другая страница", "url": "https://other.example/page", "snippet": "Ещё текст"},
    ]


def test_brave_without_a_key_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "tenders" / "_agent" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"search_provider": "brave"}', encoding="utf-8")
    _mail, search = _modules(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="ключ"):
        search.search_web("закупки")
