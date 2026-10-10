from tender_agent.api.schemas import TenderResult
from tender_agent.storage.repository import SiteProfile, profile_is_fresh
from tender_agent.tenders.discovery import fallback_cards, pick_search
from tender_agent.tenders.extraction import card_from_raw, parse_date
from tender_agent.tenders.models import Element, RawCard
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


def test_stale_profile_selector_is_not_fresh() -> None:
    assert profile_is_fresh("tender_list", 0) is False
    assert profile_is_fresh("tender_list", 2) is True
    assert profile_is_fresh("home", 0) is True
    assert SiteProfile(host="example.com").error_count == 0
