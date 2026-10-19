from tender_agent.api.schemas import TenderResult
from tender_agent.tenders.card_reading import accept_card
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


def test_card_keeps_only_quotes_and_elements_from_the_page() -> None:
    obs = Observation(
        url="https://example.com/t/1",
        text=("Поставка бумаги\nЗаказчик ГБУ «Поликлиника»\nРазмещено 15.10.2026\nНачальная цена 340 158,80 ₽\n"
              "Способ определения поставщика Запрос котировок\nЛот 1 Бумага А4\nЛот 2 Скрепки\nДокументы\nИзвещение.pdf"),
        elements=[Element(ref=3, tag="a", name="Извещение.pdf", href="https://example.com/f/1.pdf"),
                  Element(ref=4, tag="span", role="clickable", name="Проект контракта.docx")],
    )
    tender = _card(title="Поставка бумаги", url=obs.url, customer=None, price=None)
    reading = accept_card(tender, obs, {
        "customer": "ГБУ «Поликлиника»", "published": "15.10.2026", "deadline": "22.10.2026",
        "price": "340 158,80 ₽", "procurement_method": "Запрос котировок", "okpd2": "17.12.14",
        "lots": [{"number": "Лот 1", "title": "Бумага А4"}, {"number": "Лот 2", "title": "Скрепки"}],
        "documents": [{"ref": 3, "name": "Извещение.pdf"}, {"ref": 4}, {"ref": 99, "name": "чужой"}],
    })
    card = reading.tender
    assert (card.customer, card.published_at, card.deadline, card.price) == ("ГБУ «Поликлиника»", "2026-10-15", None, 340_158.8)
    assert card.details["procurement_method"] == "Запрос котировок" and "okpd2" not in card.details
    assert [lot["title"] for lot in card.details["lots"]] == ["Бумага А4", "Скрепки"]
    assert card.documents == [{"name": "Извещение.pdf", "url": "https://example.com/f/1.pdf"},
                              {"name": "Проект контракта.docx", "url": "https://example.com/t/1#file:Проект контракта.docx"}]
    assert reading.refs == [3, 4] and reading.rejected == 3  # срок, ОКПД2 и чужой файл


def test_lot_rows_become_one_procedure() -> None:
    from tender_agent.tenders.reading import split_lot

    assert split_lot("№ B0510261309243 (Лот 6)") == ("B0510261309243", "6")
    assert split_lot("32616477643 Лот № 1") == ("32616477643", "1")
    assert split_lot("0315100002526000030") == ("0315100002526000030", "")
    text = "\n".join(f"№{n} Филиал\nB0510261309243 (Лот {n})" for n in (6, 7, 8))
    obs = Observation(url="https://www.roseltorg.ru/procedures/search", text=text,
                      elements=[Element(ref=n, tag="a", name=f"№{n} Филиал", href=f"https://www.roseltorg.ru/procedure/B0510261309243/{n}")
                                for n in (6, 7, 8)])
    reading = accept_records(obs, {"records": [{"link_ref": n, "title": f"№{n} Филиал", "number": f"B0510261309243 (Лот {n})"}
                                               for n in (6, 7, 8)]}, keyword="канц", sort_required=False)
    ranked = rank_tenders(reading.accepted, sort_by=None, limit=5)
    assert len(ranked) == 1 and ranked[0].id == "B0510261309243"
    assert [lot["number"] for lot in ranked[0].details["lots"]] == ["6", "7", "8"]
    assert ranked[0].sources["title"] == "lot"  # название процедуры возьмём с карточки
    card = accept_card(ranked[0], Observation(url=ranked[0].url, text="Открытый запрос предложений на поставку бумаги"),
                       {"title": "Открытый запрос предложений на поставку бумаги"}).tender
    assert card.title == "Открытый запрос предложений на поставку бумаги"


def test_element_href_object_does_not_fail_validation() -> None:
    element = Element.model_validate({"ref": 1, "href": {}, "name": {}})
    assert element.href is None
    assert element.name == ""
