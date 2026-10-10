import asyncio
import logging

import pytest

from tender_agent.browser.downloads import safe_destination
from tender_agent.browser.navigation import validate_navigation_url
from tender_agent.config import Settings
from tender_agent.llm.base import visual_hint
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


def test_vision_and_llm_are_optional() -> None:
    assert asyncio.run(visual_hint(None, b"png", "что на экране?")) is None
    client = OpenAICompatibleClient(Settings())
    assert client.configured is False
