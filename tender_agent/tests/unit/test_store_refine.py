from pathlib import Path

from tender_agent.api.schemas import DownloadRecord, TaskResult, TaskSpec, TenderResult
from tender_agent.storage.tenders import TenderStore
from tender_agent.tenders.models import RawCard
from tender_agent.tenders.refine import apply_reading, blocks_for_model, suspicious_title


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


def test_suspicious_titles() -> None:
    for title in ["6 929 351,01 ₽", "09.10.2026 09:59", "ФЕДЕРАЛЬНОЕ ГОСУДАРСТВЕННОЕ БЮДЖЕТНОЕ УЧРЕЖДЕНИЕ",
                  "Электронный открытый аукцион", "Отказ организатора", "Подать заявку", "Конкурентный отбор"]:
        assert suspicious_title(title), title
    assert not suspicious_title("Поставка сервера DELL PowerEdge R660")


def test_apply_reading_accepts_only_quotes_from_block() -> None:
    block = "B2B-закупка\n6242593\nПРИЕМ ПРЕДЛОЖЕНИЙ\nПредоставление лицензии CommuniGate Pro\nАНО «Институт»\nНачальная цена\n6 929 351,01 ₽"
    card = RawCard(title="6 929 351,01 ₽", href="https://zakupki.mos.ru/need/6242593", block_text=block)
    todo, blocks = blocks_for_model([card])
    assert todo == [card] and blocks[0]["block"] == 0
    changed = apply_reading(todo, {"cards": [{"block": 0, "title": "Предоставление лицензии CommuniGate Pro",
                                              "number": "6242593", "customer": "ООО Выдуманное",
                                              "price": "6 929 351,01 ₽"}]})
    assert card.title == "Предоставление лицензии CommuniGate Pro" and card.sources["title"] == "llm_block"
    assert card.id == "6242593" and card.price_text == "6 929 351,01 ₽"
    assert card.customer is None  # цитаты нет в блоке — не принимаем
    assert changed == 3
