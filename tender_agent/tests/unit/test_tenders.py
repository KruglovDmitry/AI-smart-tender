from tender_agent.agent.planner import card_for_model
from tender_agent.api.schemas import TenderResult
from tender_agent.browser.observations import limit_elements
from tender_agent.storage.repository import SiteProfile, profile_is_fresh
from tender_agent.tenders.discovery import fallback_cards, pick_search, pick_submit
from tender_agent.tenders.extraction import apply_labeled_fields, apply_page_reading, card_from_raw, parse_date
from tender_agent.tenders.fields import build_fields, cards_from_blocks, known_values
from tender_agent.tenders.models import Element, Observation, PageField, RawCard
from tender_agent.tenders.ranking import rank_tenders


def _card(**kwargs: object) -> TenderResult:
    data = {
        "id": "1",
        "title": "Сервер",
        "url": "https://example.com/tenders/1",
        "verification_status": "verified",
    }
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


def test_deadline_is_not_publication_date() -> None:
    card = card_from_raw(
        RawCard(
            id="9",
            title="Поставка серверов",
            href="https://example.com/tenders/9",
            deadline="20.10.2026",
            sources={"title": "dom"},
        ),
        keyword="сервер",
        keyword_applied=False,
        sort_required=True,
    )
    assert card is not None
    assert card.published_at is None
    assert card.deadline == "2026-10-20"
    assert card.verification_status == "needs_review"
    assert card.sources["deadline_not_used_as_published"] == "true"
    assert parse_date("08.10.2026") == "2026-10-08"


def test_client_filter_drops_unrelated_title() -> None:
    skipped = card_from_raw(
        RawCard(id="2", title="Бумага", href="https://example.com/tenders/2"),
        keyword="сервер",
        keyword_applied=False,
        sort_required=False,
    )
    assert skipped is None


def test_pick_search_prefers_label_over_bare_name() -> None:
    fields = [
        Element(ref=1, tag="input", name="search", input_type="text"),
        Element(ref=2, tag="input", name="", label="Ключевое слово", placeholder="Ключевое слово", input_type="search"),
    ]
    picked = pick_search(fields, preferred="устаревший плейсхолдер")
    assert picked is not None and picked.ref == 2
    preferred = pick_search(
        [
            Element(ref=3, tag="input", placeholder="старый", input_type="text"),
            Element(ref=4, tag="input", name="search", input_type="text"),
        ],
        preferred="старый",
    )
    assert preferred is not None and preferred.ref == 3


def test_fallback_cards_from_links() -> None:
    cards = fallback_cards(
        [
            Element(ref=1, tag="a", name="Закупки", href="https://example.com/tenders"),
            Element(ref=2, tag="a", name="Поставка серверного оборудования", href="https://example.com/tenders/7"),
        ]
    )
    assert len(cards) == 1
    assert cards[0].href.endswith("/7")
    assert cards[0].sources["title"] == "link"


def test_page_reading_keeps_only_values_present_on_the_page() -> None:
    obs = Observation(
        url="https://example.com/tenders/9",
        text_excerpt="Организатор Акционерное общество «Ильменит». Окончание приема заявок 22.07.2026. Прием заявок. 1 200 000 руб.",
        elements=[
            Element(ref=4, tag="a", name="spec.pdf", href="https://example.com/files/spec.pdf", download=True),
            Element(ref=5, tag="a", name="Назад к поиску", href="https://example.com/search"),
        ],
        file_refs=[4],
    )
    tender = _card(
        id="9",
        title="Сервер",
        url="https://example.com/tenders/9",
        matched_keyword="сервер",
        verification_status="needs_review",
        published_at=None,
    )
    updated, refs = apply_page_reading(
        tender,
        obs,
        {
            "customer": "Акционерное общество «Ильменит»",
            "published_at": "22.07.2026",
            "deadline": "22.07.2026",
            "price_text": "1 200 000 руб",
            "status": "Прием заявок",
            "document_refs": [4, 5, 99],
        },
    )
    assert updated.customer == "Акционерное общество «Ильменит»"
    assert updated.deadline == "2026-07-22"
    assert updated.published_at is None
    assert updated.price == 1_200_000
    assert updated.currency == "RUB"
    assert updated.status == "Прием заявок"
    assert updated.verification_status == "needs_review"
    assert refs == [4]
    assert updated.document_urls == ["https://example.com/files/spec.pdf"]


def test_page_reading_ignores_customer_missing_from_the_page() -> None:
    obs = Observation(url="https://example.com/tenders/1", text_excerpt="Поставка серверов")
    updated, refs = apply_page_reading(
        _card(),
        obs,
        {"customer": "Выдуманный заказчик", "document_refs": [3]},
    )
    assert updated.customer is None
    assert refs == []


def test_result_block_reads_price_and_dates_without_a_site_selector() -> None:
    text = (
        "44-ФЗ Запрос котировок № 0813500000126018903 Подача заявок "
        "Объект закупки Выполнение работ по демонтажу и установке дверей "
        "Организация, осуществляющая размещение ГОСУДАРСТВЕННОЕ КАЗЕННОЕ УЧРЕЖДЕНИЕ "
        "Начальная цена 7 980 000,00 ₽ Размещено 12.10.2026 Обновлено 12.10.2026 "
        "Окончание подачи заявок 19.10.2026"
    )
    cards = cards_from_blocks(
        [{"text": text, "href": "https://zakupki.gov.ru/epz/order/notice/zk20/view/common-info.html?regNumber=0813500000126018903"}]
    )
    assert len(cards) == 1
    card = card_from_raw(cards[0], keyword="сервер", keyword_applied=True, sort_required=True)
    assert card is not None
    assert card.id == "0813500000126018903"
    assert card.title.startswith("Выполнение работ")
    assert card.price == 7_980_000
    assert card.published_at == "2026-10-12"
    assert card.deadline == "2026-10-19"
    assert card.customer.startswith("ГОСУДАРСТВЕННОЕ КАЗЕННОЕ УЧРЕЖДЕНИЕ")
    assert card.status == "Подача заявок"


def test_field_id_fills_card_from_the_pair_not_the_excerpt() -> None:
    obs = Observation(
        url="https://example.com/tenders/9",
        text_excerpt="Меню площадки",
        fields=[
            PageField(id=4, label="Начальная цена", value="7 980 000,00 ₽"),
            PageField(id=5, label="Размещено", value="12.10.2026"),
            PageField(id=6, label="Окончание подачи заявок", value="19.10.2026"),
            PageField(id=7, label="Обновлено", value="11.10.2026"),
            PageField(id=8, label="Заказчик", value="Региональный центр закупок"),
        ],
    )
    updated, _refs = apply_page_reading(
        _card(published_at=None, price=None, customer=None),
        obs,
        {"customer": 8, "published_at": 7, "deadline": 6, "price": 4},
    )
    assert updated.customer == "Региональный центр закупок"
    assert updated.published_at is None
    assert updated.deadline == "2026-10-19"
    assert updated.price == 7_980_000
    labeled = apply_labeled_fields(
        _card(published_at=None, price=None, customer=None),
        obs.fields,
    )
    assert labeled.published_at == "2026-10-12"
    assert labeled.price == 7_980_000


def test_quote_is_checked_against_the_whole_content_block() -> None:
    menu = "Главное меню " * 40
    obs = Observation(
        url="https://example.com/tenders/9",
        text_excerpt=menu[:800],
        content_text=menu + "Начальная цена 50 000 руб. Размещено 01.02.2026.",
    )
    updated, _refs = apply_page_reading(
        _card(published_at=None, price=None),
        obs,
        {"price_quote": "50 000 руб", "published_quote": "01.02.2026"},
    )
    assert updated.price == 50_000
    assert updated.published_at == "2026-02-01"


def test_card_payload_sends_fields_instead_of_the_page_text() -> None:
    obs = Observation(
        url="https://example.com/tenders/9",
        content_text="меню " * 500,
        fields=build_fields(
            [
                {"label": "Заказчик", "value": "Завод", "source": "struct"},
                {"label": "Размещено", "value": "12.10.2026", "source": "struct"},
                {"label": "Окончание подачи заявок", "value": "19.10.2026", "source": "struct"},
                {"label": "Начальная цена", "value": "10 000,00 ₽", "source": "struct"},
                {"label": "Пункт меню", "value": "Контакты", "source": "adjacent"},
            ],
            "",
        ),
    )
    payload = card_for_model(obs)
    assert "content" not in payload
    labels = [item["label"] for item in payload["fields"]]
    assert "Пункт меню" not in labels
    assert "Начальная цена" in labels


def test_limit_elements_keeps_document_links() -> None:
    controls = [Element(ref=index, tag="input", name=f"field-{index}") for index in range(1, 70)]
    pdf = Element(ref=90, tag="a", name="spec.pdf", href="https://example.com/spec.pdf", download=True)
    kept = limit_elements([*controls, pdf], limit=60)
    assert any(item.ref == 90 for item in kept)


def test_element_href_object_does_not_fail_validation() -> None:
    element = Element.model_validate({"ref": 1, "href": {}, "name": {}})
    assert element.href is None
    assert element.name == ""


def test_stale_profile_selector_is_not_fresh() -> None:
    assert profile_is_fresh("tender_list", 0) is False
    assert profile_is_fresh("tender_list", 2) is True
    assert profile_is_fresh("home", 0) is True
    assert SiteProfile(host="example.com").error_count == 0


def test_submit_is_never_the_search_field_itself() -> None:
    # Главная ЕИС: у поля поиска role="search" и type="dirname", отдельной кнопки нет.
    search = Element(ref=1, tag="input", role="search", input_type="dirname", placeholder="Введите номер или наименование")
    other = Element(ref=2, tag="input", input_type="checkbox", label="Искать точно, как в запросе")
    assert pick_submit([search, other], search) is None
    button = Element(ref=3, tag="button", name="Найти")
    assert pick_submit([search, other, button], search).ref == 3


def test_fallback_cards_skip_other_hosts_and_svg_text() -> None:
    cards = fallback_cards(
        [
            Element(ref=1, tag="a", name=".st0 { fill-rule: evenodd; } tender", href="https://example.com/tenders/1"),
            Element(ref=2, tag="a", name="Канал ЕИС о закупках", href="https://t.me/gis_eiszakupki"),
            Element(ref=3, tag="a", name="Поставка серверного оборудования", href="https://www.example.com/tenders/7"),
        ],
        "https://example.com/epz/home.html",
    )
    assert [card.href for card in cards] == ["https://www.example.com/tenders/7"]


def test_news_block_is_not_a_tender() -> None:
    news = [
        {"href": f"https://example.com/news/{i}", "text": f"12.10.2026\nНовость о работе единой информационной системы в сфере закупок номер {i}"}
        for i in range(3)
    ]
    assert cards_from_blocks(news) == []


def test_adjacent_pair_with_value_in_label_is_ignored() -> None:
    # ЕИС: соседние контейнеры дают «Объект закупки <название>» → «Заказчик …» и «Размещено <дата>» → «Обновлено <дата>».
    text = "Объект закупки\nСерверное оборудование (сетевое хранилище данных)\nРазмещено\n13.10.2026\nОбновлено\n13.10.2026"
    pairs = [
        {"label": "Объект закупки Серверное оборудование (сетевое хранилище данных)", "value": "Заказчик АНО «Центр»", "source": "adjacent"},
        {"label": "Размещено 13.10.2026", "value": "Обновлено 13.10.2026", "source": "adjacent"},
        {"label": "Объект закупки Поставка сервера", "value": "Заказчик ГБУ «Поликлиника»", "source": "adjacent"},
    ]
    known = known_values(build_fields(pairs, text))
    assert known["title"] == "Серверное оборудование (сетевое хранилище данных)"
    assert known["published"] == "13.10.2026"
