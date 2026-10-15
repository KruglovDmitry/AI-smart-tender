from tender_agent.agent.orchestrator import search_applied
from tender_agent.browser.navigation import same_site
from tender_agent.heuristics.known_sites import search_url, sorted_by_date_url
from tender_agent.heuristics.discovery import pick_sort, pick_submit
from tender_agent.heuristics.fields import junk_title
from tender_agent.tenders.models import Element, Observation, RawCard


def test_same_site_allows_bare_domain_and_subdomains() -> None:
    assert same_site("https://fabrikant.ru/v2/trades/procedure/view/x", "www.fabrikant.ru")
    assert same_site("https://utp.sberbank-ast.ru/Trade/NBT/PurchaseView/22/0/0/1", "www.sberbank-ast.ru")
    assert not same_site("https://zakupki.gov.ru/epz/order/notice", "www.fabrikant.ru")


def test_sort_option_ignores_update_date() -> None:
    update = Element(ref=1, tag="a", name="Дате обновления")
    publish = Element(ref=2, tag="a", name="Дате размещения")
    assert pick_sort([update]) is None
    assert pick_sort([update, publish]).ref == 2


def test_known_sort_url_for_eis() -> None:
    url = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString=сервер"
    sorted_url = sorted_by_date_url(url)
    assert sorted_url and "sortBy=PUBLISH_DATE" in sorted_url and "sortDirection=false" in sorted_url
    assert sorted_by_date_url(sorted_url) is None
    assert sorted_by_date_url("https://www.tektorg.ru/procedures?name=x").endswith("sort=datePublished_desc")
    assert sorted_by_date_url("https://www.b2b-center.ru/market/") is None


def test_search_applied_checks_results_not_just_url() -> None:
    cards = [RawCard(title=t, href=f"https://x.ru/{i}") for i, t in enumerate(["Линейная арматура", "Электроды", "Провода"])]
    assert not search_applied(Observation(url="https://www.tektorg.ru/procedures", cards=cards), "сервер")
    served = cards + [RawCard(title="Поставка серверного оборудования", href="https://x.ru/9"),
              RawCard(title="Сервер хранения", href="https://x.ru/10")]
    assert search_applied(Observation(url="https://www.tektorg.ru/procedures", cards=served), "сервер")
    assert search_applied(Observation(url="https://x.ru/search?q=%D1%81%D0%B5%D1%80%D0%B2%D0%B5%D1%80", cards=cards), "сервер")


def test_junk_titles() -> None:
    for text in ["Просмотр", "Избранное", "№ SBR028-2610130003.1", "ATOM13072600026 (Лот 1)", "№ 32616441786",
                 "Закупки Росатом", "Закупки по 44-ФЗ", "Госзакупки по 44-ФЗ", "ПРИЕМ ПРЕДЛОЖЕНИЙ",
                 "Коммерческие закупки и закупки по 223-ФЗ", "№ в ЕИС 32616461240",
                 "223-ФЗ и Коммерческие закупки", "Государственные закупки (44-ФЗ)", "86. Ханты-Мансийский АО (Югра)"]:
        assert junk_title(text), text
    assert not junk_title("Поставка серверного оборудования")
    assert not junk_title("Сервер")
    assert not junk_title("Закупка серверного оборудования для нужд ЦОД")


def test_submit_prefers_exact_find_over_advanced_search() -> None:
    search = Element(ref=1, tag="input", input_type="text", placeholder="Введите ключевое слово")
    advanced = Element(ref=2, tag="button", name="Расширенный поиск")
    find = Element(ref=3, tag="button", name="Найти")
    assert pick_submit([search, advanced, find], search).ref == 3
    assert pick_submit([search, advanced], search) is None


def test_known_search_url() -> None:
    assert search_url("https://www.tektorg.ru/procedures", "сервер") == "https://www.tektorg.ru/procedures?name=%D1%81%D0%B5%D1%80%D0%B2%D0%B5%D1%80"
    assert search_url("https://example.com/", "сервер") is None


def test_search_applied_uses_block_text() -> None:
    cards = [RawCard(title="Электронный открытый аукцион", href=f"https://x.ru/{i}", block_text=f"№ {i}\nПоставка сервера")
             for i in range(4)]
    assert search_applied(Observation(url="https://www.sberbank-ast.ru/UnitedPurchaseList.html", cards=cards), "сервер")


def test_eis_filestore_links_are_documents() -> None:
    from tender_agent.heuristics.discovery import file_refs

    doc = Element(ref=1, tag="a", name="Проект контракта",
                  href="https://zakupki.gov.ru/44fz/filestore/public/1.0/download/priz/file.html?uid=ABC")
    nav = Element(ref=2, tag="a", name="Журнал событий", href="https://zakupki.gov.ru/epz/order/notice/ea20/view/event-journal.html")
    assert file_refs([doc, nav]) == [1]


def test_progress_method_counters() -> None:
    from tender_agent.agent.state import Progress

    progress = Progress()
    progress.mark("search", "rule", False)
    progress.mark("search", "known_url", None)
    progress.mark_ok("search", "known_url")
    progress.mark("sort", "vision", True)
    assert progress.methods == {
        "search": {"rule": {"used": 1, "ok": 0}, "known_url": {"used": 1, "ok": 1}},
        "sort": {"vision": {"used": 1, "ok": 1}},
    }


def test_negated_download_is_not_a_download() -> None:
    from tender_agent.agent.planner import parse_task
    from tender_agent.config import Settings

    base = 'С площадки https://zakupki.gov.ru/ верни последние 5 закупок по ключевому слову "канцтовары".'
    assert parse_task(base + " Скачай документы.", Settings()).download_documents
    for tail in (" Ничего не скачивай.", " Документы не скачивай.", " Без скачивания."):
        assert not parse_task(base + tail, Settings()).download_documents, tail


def test_lots_of_one_procedure_are_one_tender() -> None:
    from tender_agent.api.schemas import TenderResult
    from tender_agent.tenders.ranking import rank_tenders

    lots = [TenderResult(id="B0510261309243", title=f"Филиал {i}", url=f"https://www.roseltorg.ru/procedure/B0510261309243/{i}",
                         verification_status="verified") for i in (1, 6, 7)]
    other = TenderResult(id="0373100110426000032", title="Канцтовары", url="https://www.roseltorg.ru/procedure/0373100110426000032/1",
                         verification_status="verified")
    ranked = rank_tenders([*lots, other], sort_by=None, limit=5)
    assert [t.id for t in ranked] == ["B0510261309243", "0373100110426000032"]
    assert [lot["url"][-1] for lot in ranked[0].details["lots"]] == ["1", "6", "7"]


def test_known_sort_url_for_roseltorg() -> None:
    url = "https://www.roseltorg.ru/procedures/search?sale=1&query_field=x"
    assert sorted_by_date_url(url).endswith("sort=date_published_down")
