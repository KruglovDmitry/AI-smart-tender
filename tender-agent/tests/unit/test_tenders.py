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


def test_results_keep_only_relevant_active_records_and_merge_dash_lots():
    from tender_agent.tenders.reading import accept_records

    text = ("Канцтовары для школы 5582689-1 Приём заявок до 30.10.2026 "
            "Канцтовары для офиса 5582689-2 Приём заявок до 30.10.2026 "
            "Перевозка спирта канцтовары 777 Приём заявок "
            "Бумага и канцтовары 32211596763 Закупка завершена "
            "Канцтовары 1203219-10829500001126006716 Приём заявок до 01.10.2026")
    elements = [Element(ref=n, tag="a", name=f"запись {n}", href=f"https://x.ru/t/{n}") for n in range(1, 6)]
    obs = Observation(url="https://x.ru/search", text=text, elements=elements)
    records = [
        {"link_ref": 1, "title": "Канцтовары для школы", "number": "5582689-1", "deadline": "30.10.2026", "relevant": True},
        {"link_ref": 2, "title": "Канцтовары для офиса", "number": "5582689-2", "deadline": "30.10.2026", "relevant": True},
        {"link_ref": 3, "title": "Перевозка спирта", "number": "777", "relevant": False},
        {"link_ref": 4, "title": "Бумага и канцтовары", "number": "32211596763", "status": "Закупка завершена"},
        {"link_ref": 5, "title": "Канцтовары", "number": "1203219-10829500001126006716", "deadline": "01.10.2026"},
    ]
    reading = accept_records(obs, {"records": records}, keyword="канцтовары", sort_required=False,
                             active_only=True, today="2026-10-22")
    assert [(t.id, t.details.get("lot")) for t in reading.accepted] == [("5582689", "1"), ("5582689", "2")]
    assert reading.rejected == {"не по теме запроса": 1, "приём заявок закончен": 2}
    archive = accept_records(obs, {"records": records}, keyword="канцтовары", sort_required=False)
    assert [t.id for t in archive.accepted][-1] == "1203219-10829500001126006716"  # один номер с хвостом — не лот


def test_archive_request_turns_active_filter_off():
    from tender_agent.agent.planner import parse_task
    from tender_agent.config import Settings

    base = 'С площадки https://zakupki.gov.ru/ верни последние 5 закупок по ключевому слову "канцтовары"'
    assert parse_task(base, Settings()).active_only
    assert not parse_task(base + ", включая завершённые", Settings()).active_only


def test_page_dates_include_rejected_records():
    from tender_agent.tenders.reading import accept_records

    obs = Observation(url="https://x.ru/s", text="Сервер 1 22.10.2026 Сервер 2 15.10.2026 Сервер 3 09.10.2026 завершена",
                      elements=[Element(ref=n, tag="a", name=f"Сервер {n}", href=f"https://x.ru/t/{n}") for n in (1, 2, 3)])
    records = [{"link_ref": 1, "title": "Сервер 1", "published": "22.10.2026"},
               {"link_ref": 2, "title": "Сервер 2", "published": "15.10.2026", "status": "завершена"},
               {"link_ref": 3, "title": "Сервер 3", "published": "09.10.2026", "status": "завершена"}]
    reading = accept_records(obs, {"records": records}, keyword="сервер", sort_required=True, active_only=True,
                             today="2026-10-23")
    assert len(reading.accepted) == 1 and reading.published == ["2026-10-22", "2026-10-15", "2026-10-09"]


def test_closed_tail_after_last_active_record():
    from tender_agent.agent.orchestrator import _closed_tail
    from tender_agent.tenders.reading import accept_records

    dates = ["22.10.2026", "15.10.2026", "09.10.2026", "08.10.2026", "25.09.2026", "14.09.2026", "09.09.2026"]
    names = [f"Сервер {n}" for n in range(1, 8)]
    obs = Observation(url="https://x.ru/s", text=" ".join(f"{n} {d} завершена" for n, d in zip(names, dates)),
                      elements=[Element(ref=n, tag="a", name=names[n - 1], href=f"https://x.ru/t/{n}") for n in range(1, 8)])
    records = [{"link_ref": n, "title": names[n - 1], "published": dates[n - 1],
                **({} if n <= 2 else {"status": "завершена"})} for n in range(1, 8)]
    reading = accept_records(obs, {"records": records}, keyword="сервер", sort_required=True, active_only=True,
                             today="2026-10-26")
    assert len(reading.accepted) == 2 and len(reading.closed_tail) == 5
    assert _closed_tail(reading.closed_tail, reading.accepted)
    # Закрытые среди сегодняшних (ЕИС, картриджи) — не хвост старых закупок.
    assert not _closed_tail(["2026-10-26"] * 5, reading.accepted)


def test_results_window_starts_after_read_records():
    from tender_agent.tenders.reading import RESULTS_TEXT_FOR_MODEL, results_window

    rows = [f"Картридж {n} SP{1000 + n // 5} (Лот {n}) Прием заявок " + "x" * 400 for n in range(120)]
    elements = [Element(ref=n, tag="a", name=f"Картридж {n}", href=f"https://x.ru/t/{n}") for n in range(120)]
    obs = Observation(url="https://x.ru/s", text=" ".join(rows), elements=elements)
    text, links = results_window(obs, [])
    assert text.startswith("Картридж 0") and len(text) == RESULTS_TEXT_FOR_MODEL
    text, links = results_window(obs, ["SP1009 (Лот 45)"])
    assert "Картридж 49 " in text and "Картридж 40 " not in text and "Картридж 90 " in text
    names = {link["text"] for link in links}
    assert "Картридж 49" in names and "Картридж 30" not in names


def test_sort_desc_in_url():
    from tender_agent.agent.orchestrator import _SORT_DESC_URL

    assert _SORT_DESC_URL.search("https://www.roseltorg.ru/procedures/search?query_field=x&sort=date_published_down")
    assert _SORT_DESC_URL.search("https://zakupki.mos.ru/purchase/list?sortField=PublishDate&sortDesc=true")
    assert not _SORT_DESC_URL.search("https://zakupki.gov.ru/epz/order/extendedsearch/results.html?sortBy=PUBLISH_DATE")


def test_closed_after_card_status_and_sole_supplier():
    from tender_agent.tenders.reading import closed_after_card

    done = TenderResult(title="ИБП", url="https://x.ru/1", status="Проведена")
    sole = TenderResult(title="ИБП", url="https://x.ru/2", status="Опубликована",
                        details={"procurement_method": "Прямая закупка у единственного поставщика"})
    open_ = TenderResult(title="ИБП", url="https://x.ru/3", status="Опубликована", deadline="2026-11-02")
    assert closed_after_card(done, "2026-10-26") and closed_after_card(sole, "2026-10-26")
    assert not closed_after_card(open_, "2026-10-26")


def test_task_from_chat_without_scheme_and_quotes():
    from tender_agent.agent.planner import parse_task
    from tender_agent.config import Settings

    cases = {
        "Найди на zakupki.gov.ru 5 последних закупок ИБП, включая завершённые, и скачай документы": ("https://zakupki.gov.ru", "ИБП"),
        "Найди на https://zakupki.gov.ru 5 последних закупок по ключевому слову ИБП, включая завершённые": ("https://zakupki.gov.ru", "ИБП"),
        "Найди на zakupki.gov.ru 3 последние закупки ИБП и скачай документы": ("https://zakupki.gov.ru", "ИБП"),
        'С площадки https://www.roseltorg.ru/ верни последние 5 закупок по ключевому слову "картриджи"': ("https://www.roseltorg.ru/", "картриджи"),
        "Найди закупки серверного оборудования на www.sberbank-ast.ru": ("https://www.sberbank-ast.ru", "серверного оборудования"),
    }
    for text, (url, topic) in cases.items():
        task = parse_task(text, Settings())
        assert (task.url, task.topic) == (url, topic), text
    assert parse_task("Найди на zakupki.gov.ru 3 последние закупки ИБП (источник бесперебойного питания)", Settings()).limit == 3
    task = parse_task("Найди на https://zakupki.gov.ru 3 последние закупки ИБП (источник бесперебойного питания) и скачай", Settings())
    assert task.topic == "ИБП"
    assert parse_task('С площадки https://x.ru верни последние 10 закупок по ключевому слову "ИБП"', Settings()).limit == 10
