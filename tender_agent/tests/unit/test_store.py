from pathlib import Path

from tender_agent.api.schemas import DownloadRecord, TaskResult, TaskSpec, TenderResult
from tender_agent.storage.tenders import TenderStore


def _result(*tenders: TenderResult, downloads: list[DownloadRecord] | None = None) -> TaskResult:
    task = TaskSpec(url="https://www.sberbank-ast.ru/", topic="сервер", keywords=["сервер"])
    return TaskResult(status="completed", task=task, results=list(tenders), downloads=downloads or [])


def test_store_upserts_keeps_known_values_and_marks_new(tmp_path: Path) -> None:
    store = TenderStore(tmp_path / "agent.sqlite3")
    first = TenderResult(id="0310100006626000022", title="Приобретение сервера", url="https://44.sberbank-ast.ru/x?id=1",
                         price=2151000.0, published_at="2026-10-13", verification_status="verified",
                         document_urls=["https://44.sberbank-ast.ru/doc/1.pdf"],
                         documents=[{"name": "Извещение.pdf", "url": "https://44.sberbank-ast.ru/doc/1.pdf"}])
    saved = store.save_run("задание", _result(first))
    assert saved["new"] == 1
    again = TenderResult(id="0310100006626000022", title="Приобретение сервера", url="https://44.sberbank-ast.ru/x?id=1",
                         price=None, deadline="2026-10-22", verification_status="verified")
    download = DownloadRecord(source_url="https://44.sberbank-ast.ru/doc/1.pdf", path="/data/x/1.pdf", name="1.pdf",
                              size=100, status="saved", tender_url="https://44.sberbank-ast.ru/x?id=1")
    saved = store.save_run("задание", _result(again, downloads=[download]))
    assert saved["new"] == 0 and store.keys_new_in_run(saved["run_id"]) == []
    rows = store.search(platform="sberbank-ast.ru")
    assert len(rows) == 1
    row = rows[0]
    assert row["platform"] == "sberbank-ast.ru"  # поддомен карточки сведён к площадке запуска
    assert row["price"] == 2151000.0 and row["deadline"] == "2026-10-22" and row["seen_count"] == 2
    assert row["documents"] == [{"name": "Извещение.pdf", "url": "https://44.sberbank-ast.ru/doc/1.pdf", "local_path": "/data/x/1.pdf"}]

    # Мягкое удаление: не видна в поиске; найденная снова — обновляется, но остаётся удалённой.
    assert store.set_deleted(row["id"]) and store.search(platform="sberbank-ast.ru") == []
    saved = store.save_run("задание", _result(again))
    assert saved["new"] == 0 and store.search(platform="sberbank-ast.ru") == []
    assert store.set_deleted(row["id"], False) and store.search(platform="sberbank-ast.ru")[0]["seen_count"] == 3
    assert not store.set_deleted(10_000)
