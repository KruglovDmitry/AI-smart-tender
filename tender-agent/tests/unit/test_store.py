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


def test_same_tender_from_another_platform_is_one_record(tmp_path: Path) -> None:
    store = TenderStore(tmp_path / "agent.sqlite3")

    def run(url: str, *found: TenderResult) -> dict:
        task = TaskSpec(url=url, topic="канцтовары", keywords=["канцтовары"])
        return store.save_run("задание", TaskResult(status="completed", task=task, results=list(found)))

    eis = TenderResult(id="32616477643", title="Поставка канцтоваров", url="https://zakupki.gov.ru/223/x?regNumber=32616477643")
    assert run("https://zakupki.gov.ru/", eis)["new"] == 1
    roseltorg = TenderResult(id="32616477643", title="Поставка канцтоваров", url="https://www.roseltorg.ru/procedure/32616477643/1",
                             deadline="2026-10-28")
    assert run("https://www.roseltorg.ru/", roseltorg)["new"] == 0
    rows = store.search()
    assert len(rows) == 1 and rows[0]["details"]["also_on"] == ["roseltorg.ru"] and rows[0]["deadline"] == "2026-10-28"

    # Разные номера, но то же название и заказчик (Росэлторг и Фабрикант).
    a = TenderResult(id="B0510261309243", title="Поставка бумаги, канцтоваров и материалов для охраны труда",
                     customer='ООО "РЕСУРСТРАНС"', url="https://www.roseltorg.ru/procedure/B0510261309243")
    b = TenderResult(id="5582689", title="Поставка бумаги, канцтоваров и материалов для охраны труда",
                     customer="ООО «Ресурстранс»", url="https://www.fabrikant.ru/trades/5582689")
    run("https://www.roseltorg.ru/", a)
    assert run("https://www.fabrikant.ru/", b)["new"] == 0
    # Короткий внутренний номер другой площадки — не повод склеивать разные закупки.
    other = TenderResult(id="5582689", title="Канцтовары для школы", url="https://www.tektorg.ru/p/5582689")
    assert run("https://www.tektorg.ru/", other)["new"] == 1
    assert len(store.search()) == 3


def test_platform_prefixed_number_or_inn_suffix_still_matches(tmp_path: Path) -> None:
    store = TenderStore(tmp_path / "agent.sqlite3")

    def run(url: str, tender: TenderResult) -> dict:
        task = TaskSpec(url=url, topic="ИБП", keywords=["ИБП"])
        return store.save_run("задание", TaskResult(status="completed", task=task, results=[tender]))

    run("https://www.roseltorg.ru/", TenderResult(id="SP10325142", title="ИБП Сайбер Электро ЭКСПЕРТ-II-6000Р 6000В",
                                                  customer="ГБУЗ «МНПЦДК ДЗМ»", url="https://www.roseltorg.ru/procedure/SP10325142/1"))
    assert run("https://zakupki.mos.ru/", TenderResult(id="10325142", title="ИБП Сайбер Электро ЭКСПЕРТ-II-6000Р 6000В",
                                                       url="https://zakupki.mos.ru/auction/10325142"))["new"] == 0
    run("https://www.roseltorg.ru/", TenderResult(id="SP10325021", title="ИБП AccordTec ББП-20 (AT-02334)",
                                                  customer="ГБУЗ «ГП № 36 ДЗМ»", url="https://www.roseltorg.ru/procedure/SP10325021/1"))
    assert run("https://zakupki.mos.ru/", TenderResult(id="999", title="ИБП AccordTec ББП-20 (AT-02334)",
                                                       customer="ГБУЗ «ГП № 36 ДЗМ» (ИНН: 7723103970)",
                                                       url="https://zakupki.mos.ru/auction/999"))["new"] == 0
    assert len(store.search()) == 2


def test_same_card_with_differently_read_number_is_one_record(tmp_path: Path) -> None:
    # Росатом: в первый раз модель прочитала «242340 (5116123)», во второй — «242340».
    store = TenderStore(tmp_path / "agent.sqlite3")

    def run(*found: TenderResult) -> dict:
        task = TaskSpec(url="https://zakupki.rosatom.ru/", topic="ИБП", keywords=["ИБП"])
        return store.save_run("задание", TaskResult(status="completed", task=task, results=list(found)))

    card = "https://zakupki.rosatom.ru/?link=procurements&obj_id=khxx"
    assert run(TenderResult(id="242340 (5116123)", title="Модернизация ИБП", url=card))["new"] == 1
    assert run(TenderResult(id="242340", title="Модернизация ИБП", url=card))["new"] == 0
    # Тот же адрес (адрес выдачи), но другой номер — другая закупка.
    assert run(TenderResult(id="242341", title="Замена ИБП", url=card))["new"] == 1
    assert len(store.search()) == 2
