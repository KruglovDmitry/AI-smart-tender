from tender_agent.api.schemas import TenderResult
from tender_agent.tenders.extraction import card_from_raw, parse_date, parse_price
from tender_agent.tenders.models import Element, Observation, RawCard
from tender_agent.tenders.ranking import rank_tenders
from tender_agent.tenders.reading import accept_records


def _card(**kwargs: object) -> TenderResult:
    data = {"id": "1", "title": "Сервер", "url": "https://example.com/tenders/1", "verification_status": "verified"}
    data.update(kwargs)
    return TenderResult(**data)


def test_rank_uses_publication_date_and_drops_duplicates() -> None:
    ranked = rank_tenders(
        [
            _card(id="1", url="https://example.com/a", published_at="2026-01-01"),
            _card(id="1", url="https://example.com/a", published_at="2026-01-01", title="повтор"),
            _card(id="2", url="https://example.com/b", published_at=None),
            _card(id="3", url="https://example.com/c", published_at="2026-10-08"),
        ],
        sort_by="published_at",
        limit=2,
    )
    assert [item.id for item in ranked] == ["3", "1"]


def test_lots_of_one_procedure_are_one_tender() -> None:
    lots = [_card(id="B0510261309243", title=f"Филиал {i}", url=f"https://www.roseltorg.ru/procedure/B0510261309243/{i}")
            for i in (1, 6, 7)]
    other = _card(id="0373100110426000032", title="Канцтовары", url="https://www.roseltorg.ru/procedure/0373100110426000032/1")
    ranked = rank_tenders([*lots, other], sort_by=None, limit=5)
    assert [t.id for t in ranked] == ["B0510261309243", "0373100110426000032"]
    assert [lot["url"][-1] for lot in ranked[0].details["lots"]] == ["1", "6", "7"]


def test_deadline_is_not_publication_date() -> None:
    card = card_from_raw(
        RawCard(id="9", title="Поставка серверов", href="https://example.com/tenders/9", deadline="20.10.2026"),
        keyword="сервер",
        sort_required=True,
    )
    assert card is not None
    assert (card.published_at, card.deadline) == (None, "2026-10-20")
    assert card.verification_status == "needs_review"  # для «последних» нужна дата публикации


def test_title_without_keyword_needs_review() -> None:
    card = card_from_raw(RawCard(title="Бумага", href="https://example.com/tenders/2"), keyword="сервер", sort_required=False)
    assert card is not None and card.verification_status == "needs_review"


def test_dates_and_prices() -> None:
    assert parse_date("21.09.26 16:51 (МСК)") == "2026-09-21"
    assert parse_date("08.10.2026") == "2026-10-08"
    assert parse_date("2026-10-08T10:00") == "2026-10-08"
    assert parse_date("16 окт. 2026 10:53") == "2026-10-16"
    assert parse_date("3 мая 2026") == "2026-05-03"
    assert parse_price(None, "7 980 000,00 ₽", None) == (7_980_000.0, "RUB")
    assert parse_price(None, "1 200 000 руб.", None) == (1_200_000.0, "RUB")


def test_records_are_checked_against_the_page() -> None:
    obs = Observation(
        url="https://example.com/search?q=сервер",
        text=("Результаты поиска\nПоставка серверного оборудования\n№ 0813500000126018903\n"
              "Заказчик ГБУ «Поликлиника»\nРазмещено 12.10.2026\nНачальная цена 7 980 000,00 ₽\n"
              "Канцтовары для офиса\nРазмещено 11.10.2026"),
        elements=[Element(ref=7, tag="a", name="Поставка серверного оборудования", href="https://example.com/t/1"),
                  Element(ref=8, tag="a", name="Канцтовары для офиса", href="https://example.com/t/2")],
    )
    reading = accept_records(obs, {"records": [
        {"link_ref": 7, "title": "Поставка серверного оборудования", "number": "№ 0813500000126018903",
         "customer": "ООО Выдуманное", "published": "12.10.2026", "price": "7 980 000,00 ₽"},
        {"link_ref": 8, "title": "Канцтовары для офиса", "published": "11.10.2026"},
        {"link_ref": 99, "title": "Канцтовары для офиса"},
        {"link_ref": 7, "title": "Закупка, которой нет на странице"},
    ]}, keyword="сервер", sort_required=True)
    assert reading.found == 4
    assert reading.rejected == {"нет ссылки на карточку": 1, "название не найдено на странице": 1}
    first, second = reading.accepted
    assert (first.id, first.url, first.published_at, first.price) == ("0813500000126018903", "https://example.com/t/1",
                                                                       "2026-10-12", 7_980_000.0)
    assert first.customer is None  # цитаты нет на странице — не принимаем
    assert first.verification_status == "verified" and second.verification_status == "needs_review"


def test_element_href_object_does_not_fail_validation() -> None:
    element = Element.model_validate({"ref": 1, "href": {}, "name": {}})
    assert element.href is None
    assert element.name == ""
