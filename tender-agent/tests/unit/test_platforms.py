from tender_agent.agent.loop import page_for_model
from tender_agent.agent.planner import parse_task
from tender_agent.browser.navigation import same_site
from tender_agent.config import Settings
from tender_agent.tenders.models import Element, Observation


def test_same_site_allows_bare_domain_and_subdomains() -> None:
    assert same_site("https://fabrikant.ru/v2/trades/procedure/view/x", "www.fabrikant.ru")
    assert same_site("https://utp.sberbank-ast.ru/Trade/NBT/PurchaseView/22/0/0/1", "www.sberbank-ast.ru")
    assert not same_site("https://zakupki.gov.ru/epz/order/notice", "www.fabrikant.ru")


def test_negated_download_is_not_a_download() -> None:
    base = 'С площадки https://zakupki.gov.ru/ верни последние 5 закупок по ключевому слову "канцтовары".'
    assert parse_task(base + " Скачай документы.", Settings()).download_documents
    for tail in (" Ничего не скачивай.", " Документы не скачивай.", " Без скачивания."):
        assert not parse_task(base + tail, Settings()).download_documents, tail


def test_cards_reread_one_by_one_only_on_errors_or_tab_limit() -> None:
    import asyncio
    from collections import Counter

    from tender_agent.agent.cards import CardReader
    from tender_agent.api.schemas import TenderResult

    def reader(first_pass: str, main_tab_works: bool):
        cards = CardReader.__new__(CardReader)
        cards.outcomes = Counter()
        calls: list[tuple[str, bool]] = []

        async def fake(tender, reuse_main=False):
            calls.append((tender.id, reuse_main))
            outcome = ("done" if main_tab_works else first_pass) if reuse_main else first_pass
            cards.outcomes[outcome] += 1
            return tender, outcome

        cards._read_in_tab = fake
        return cards, calls

    tenders = [TenderResult(id=str(i), url=f"https://x.ru/{i}") for i in range(3)]
    # Лимит вкладок (Росатом): в основной вкладке читается — перечитываем все по одной.
    limited, calls = reader("tab_limit", main_tab_works=True)
    asyncio.run(limited.read_all(tenders))
    assert [c for c in calls if c[1]] == [("0", True), ("1", True), ("2", True)]
    assert limited.outcomes["done"] == 3 and limited.outcomes["tab_limit"] == 0
    # Ошибки, но и в основной вкладке не читается — дальше одной пробы не идём.
    broken, calls = reader("error", main_tab_works=False)
    asyncio.run(broken.read_all(tenders))
    assert [c for c in calls if c[1]] == [("0", True)]
    # Документы за регистрацией (РосТендер) — настоящее ограничение: даже пробу не делаем.
    locked, calls = reader("login", main_tab_works=True)
    asyncio.run(locked.read_all(tenders))
    assert [c for c in calls if c[1]] == [] and locked.outcomes["login"] == 3


def test_action_error_names_the_blocking_element() -> None:
    from tender_agent.agent.loop import action_error

    timeout = TimeoutError(
        "Locator.click: Timeout 8000ms exceeded.\nCall log:\n  - waiting for locator\n"
        '    - <div class="onboarding-modal">…</div> intercepts pointer events\n  - retrying click action'
    )
    assert action_error(timeout).startswith('элемент перекрыт другим элементом <div class="onboarding-modal">')
    assert action_error(RuntimeError("Элемент 5 нет на текущей странице.")) == "Элемент 5 нет на текущей странице."


def test_page_for_model_lists_elements_without_conclusions() -> None:
    obs = Observation(
        url="https://www.rts-tender.ru/poisk",
        title="Поиск",
        text="Поиск закупок\n" + "x" * 10_000,
        elements=[
            Element(ref=1, tag="input", input_type="text", placeholder="Введите запрос", value="канц"),
            Element(ref=2, tag="button", name="Найти"),
            Element(ref=3, tag="a", name="Следующая", href="https://www.rts-tender.ru/poisk?page=2"),
            Element(ref=4, tag="a", name="Telegram", href="https://t.me/rts"),
        ],
        scroll={"y": 0, "height": 5000, "viewport": 900},
    )
    page = page_for_model(obs)
    assert page["elements"] == [
        "[1] input[text] placeholder=«Введите запрос» value=«канц»",
        "[2] button «Найти»",
        "[3] a «Следующая» → /poisk?page=2",
        "[4] a «Telegram» → https://t.me/rts",
    ]
    assert len(page["text"]) == 6000 and page["text_truncated"]
    assert page["scroll"].startswith("прокручено 0 из 5000")
    assert not {"search_ref", "sort_ref", "next_ref", "page_kind"} & set(page)
