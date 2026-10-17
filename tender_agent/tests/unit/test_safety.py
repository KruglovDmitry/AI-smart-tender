import logging

import pytest

from tender_agent.browser.downloads import safe_destination
from tender_agent.browser.navigation import validate_navigation_url
from tender_agent.config import Settings
from tender_agent.llm.provider import OpenAICompatibleClient
from tender_agent.observability.logging import get_logger


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/secret",
        "http://169.254.169.254/latest",
        "http://localhost/tenders",
        "http://10.1.1.1/",
        "file:///etc/passwd",
        "http://example.test",
    ],
)
def test_blocked_targets(url: str) -> None:
    settings = Settings(allowed_hosts=("example.com",))
    ok, _reason = validate_navigation_url(url, settings)
    assert ok is False


def test_public_and_allowlist() -> None:
    settings = Settings(allowed_hosts=("example.com",))
    assert validate_navigation_url("https://example.com/tenders", settings)[0] is True
    assert validate_navigation_url("https://www.example.com/tenders", settings)[0] is True
    assert validate_navigation_url("https://tenders.example.test/tenders", Settings())[0] is True


def test_mojibake_file_names_are_repaired() -> None:
    from tender_agent.browser.downloads import fix_mojibake

    broken = "Документация канцтовары.docx".encode("utf-8").decode("latin-1")
    assert fix_mojibake(broken) == "Документация канцтовары.docx"
    assert fix_mojibake("Проект контракта.docx") == "Проект контракта.docx"
    assert fix_mojibake("spec.pdf") == "spec.pdf"
    assert fix_mojibake("=?UTF-8?B?0JfQmtGB0LzQvyDQutCw0L3RhtGC0L7QstCw0YDRiy5kb2N4?=") == "ЗКсмп канцтовары.docx"


def test_encoded_file_name_wins_over_placeholder() -> None:
    from tender_agent.browser.downloads import _filename

    header = ("attachment; filename=\"____________ __________.docx\"; "
              "filename*=utf-8''%D0%94%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D0%B0%D1%86%D0%B8%D1%8F.docx")
    assert _filename(header, "https://corp.roseltorg.ru/api/v1/documents/x") == "Документация.docx"
    assert _filename('attachment; filename="spec.pdf"', "https://x/y") == "spec.pdf"


def test_download_path_stays_in_workdir(tmp_path) -> None:
    saved = safe_destination(tmp_path, "../secret.pdf")
    assert saved.is_relative_to(tmp_path.resolve())
    assert saved.parent == tmp_path.resolve()


def test_logs_redact_secrets(caplog: pytest.LogCaptureFixture) -> None:
    logger = get_logger()
    with caplog.at_level(logging.INFO, logger="tender_agent"):
        logger.info("api_key=super-secret token=abc")
    assert "super-secret" not in caplog.text
    assert "***" in caplog.text


def test_llm_is_optional() -> None:
    client = OpenAICompatibleClient(Settings())
    assert client.configured is False
