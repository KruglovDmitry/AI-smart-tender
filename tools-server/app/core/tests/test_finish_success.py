"""Unit tests for structural finish success gate."""

from __future__ import annotations

from app.agent.tools.finish_platform_task import resolve_finish_success


def test_success_with_processed() -> None:
    ok, summary = resolve_finish_success(
        True,
        processed_tenders=[{"tender_id": "1"}],
        downloaded_files=[],
        summary="ok",
    )
    assert ok is True
    assert summary == "ok"


def test_success_with_downloads() -> None:
    ok, _ = resolve_finish_success(
        True,
        processed_tenders=[],
        downloaded_files=["a.pdf"],
        summary="downloaded",
    )
    assert ok is True


def test_search_only_without_facts() -> None:
    ok, summary = resolve_finish_success(
        True,
        processed_tenders=[],
        downloaded_files=[],
        task_hint="Сценарий 2: только поиск, не открывай карточки",
        summary="search done",
    )
    assert ok is True
    assert summary == "search done"


def test_empty_success_rejected() -> None:
    ok, summary = resolve_finish_success(
        True,
        processed_tenders=[],
        downloaded_files=[],
        task_hint="полный мониторинг",
        summary="всё готово, обработал 3 тендера",
    )
    assert ok is False
    assert "ОТКЛОНЕНО" in summary


def test_explicit_failure_kept() -> None:
    ok, summary = resolve_finish_success(
        False,
        processed_tenders=[{"tender_id": "1"}],
        downloaded_files=["a.pdf"],
        summary="captcha",
    )
    assert ok is False
    assert summary == "captcha"
