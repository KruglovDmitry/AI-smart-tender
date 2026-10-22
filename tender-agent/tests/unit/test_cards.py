"""Карточки: переход на площадку-источник, причины остановки, ссылки-пустышки."""

from tender_agent.agent.cards import blocked_kind
from tender_agent.agent.loop import Loop
from tender_agent.agent.state import Progress
from tender_agent.api.schemas import TenderResult
from tender_agent.config import Settings
from tender_agent.tenders.card_reading import accept_card
from tender_agent.tenders.models import Element, Observation


def _loop(follow: bool) -> Loop:
    return Loop(runtime=None, settings=Settings(), task=None, llm=None, host="www.b2b-center.ru",
                progress=Progress(), errors=[], deadline=0, follow_links=follow)


def test_card_follows_only_links_present_on_the_page():
    source = Element(ref=5, tag="a", name="Закупка №2051792", href="https://market.otc.ru/ProductRequestGroup/Index/2051792")
    obs = Observation(url="https://www.b2b-center.ru/search/number/l2051792/", elements=[source])
    assert _loop(True)._link_host("click", {"ref": 5}, source, obs) == "market.otc.ru"
    assert _loop(True)._link_host("goto", {"url": source.href}, None, obs) == "market.otc.ru"
    assert _loop(True)._link_host("goto", {"url": "https://evil.example/"}, None, obs) == "www.b2b-center.ru"
    assert _loop(False)._link_host("click", {"ref": 5}, source, obs) == "www.b2b-center.ru"  # выдача — только свой сайт


def test_blocked_kind_tells_login_captcha_and_tab_limit_apart():
    assert blocked_kind("Требуется дополнительная проверка: разверните картинку", None) == "captcha"
    assert blocked_kind("Зарегистрируйтесь, чтобы получить доступ к документации", None) == "login"
    tabs = Observation(url="https://zakupki.rosatom.ru/", text="Превышено максимальное количество одновременно открытых вкладок")
    assert blocked_kind("Карточка не отображается", tabs) == "tab_limit"
    assert blocked_kind("На странице CAPTCHA.", Observation(url="https://x.ru/", page_kind="captcha")) == "captcha"


def test_same_page_hash_link_is_not_a_document():
    page = "https://rostender.info/region/95841975-tender-kanctovary"
    obs = Observation(url=page, text="Документация 1 файл", elements=[
        Element(ref=1, tag="a", name="1 файл", href=page + "#"),
        Element(ref=2, tag="a", name="ТЗ.docx", href="https://rostender.info/files/tz.docx"),
    ])
    reading = accept_card(TenderResult(id="95841975", url=page), obs, {"documents": [{"ref": 1}, {"ref": 2}]})
    assert [d["name"] for d in reading.tender.documents] == ["ТЗ.docx"] and reading.rejected == 1
