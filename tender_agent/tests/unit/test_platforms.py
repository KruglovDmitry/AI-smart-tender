from tender_agent.agent.orchestrator import search_applied
from tender_agent.browser.navigation import same_site
from tender_agent.sites.known import search_url, sorted_by_date_url
from tender_agent.tenders.discovery import pick_sort, pick_submit
from tender_agent.tenders.fields import junk_title
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
