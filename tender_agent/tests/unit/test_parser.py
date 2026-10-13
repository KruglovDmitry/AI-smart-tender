from tender_agent.agent.planner import TaskParseError, parse_task
from tender_agent.config import Settings


def _parse(text: str, **kwargs: int) -> object:
    return parse_task(text, Settings(**kwargs))


def test_examples_from_spec() -> None:
    first = _parse(
        "Начни мониторинг площадки https://example.com/tenders и верни последние 5 закупок на тему серверного оборудования"
    )
    assert first.url == "https://example.com/tenders"
    assert first.topic == "серверного оборудования"
    assert first.limit == 5
    assert first.sort_by == "published_at"
    assert first.download_documents is False
    assert first.max_actions == 40

    second = _parse(
        'С площадки https://example.com верни последние пять закупок по ключевому слову "медицинское оборудование"'
    )
    assert second.url == "https://example.com"
    assert second.keywords == ["медицинское оборудование"]
    assert second.limit == 5
    assert second.sort_by == "published_at"

    third = _parse(
        'Открой https://example.com/tenders, найди закупки по ключевому слову "строительство дорог", '
        "установи фильтр по дате публикации, если он доступен, и верни последние 10 закупок"
    )
    assert third.limit == 10
    assert third.filters["published_at"] == "latest"
    assert third.sort_by == "published_at"
    assert third.topic == "строительство дорог"

    fourth = _parse(
        'Найди закупки по ключевому слову "компьютеры" на https://example.com и скачай документы из первых пяти подходящих карточек'
    )
    assert fourth.download_documents is True
    assert fourth.limit == 5
    assert fourth.sort_by is None
    assert fourth.url == "https://example.com"


def test_missing_url_and_keyword() -> None:
    try:
        _parse("верни последние закупки по серверам")
    except TaskParseError as exc:
        assert "URL" in str(exc)
    else:
        raise AssertionError("ожидалась ошибка без URL")
    try:
        _parse("Открой https://example.com/tenders и посмотри площадку")
    except TaskParseError as exc:
        assert "ключев" in str(exc).lower() or "тем" in str(exc).lower()
    else:
        raise AssertionError("ожидалась ошибка без критерия")
