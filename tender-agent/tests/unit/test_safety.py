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


def test_write_unique_keeps_different_files_with_same_name(tmp_path):
    from tender_agent.browser.downloads import write_unique

    first = write_unique(tmp_path, "НМЦК.xls", b"one")
    second = write_unique(tmp_path, "НМЦК.xls", b"two")
    again = write_unique(tmp_path, "НМЦК.xls", b"one")  # повторный прогон не плодит копии
    assert first.name == "НМЦК.xls" and second.name == "НМЦК_2.xls" and again == first
    assert sorted(p.read_bytes() for p in tmp_path.iterdir()) == [b"one", b"two"]


def test_write_unique_dedups_same_bytes_under_different_names(tmp_path):
    from tender_agent.browser.downloads import write_unique

    first = write_unique(tmp_path, "Общие сведения.docx", b"%PDF-1.4 same")
    again = write_unique(tmp_path, "Общие сведения_2.docx", b"%PDF-1.4 same")
    assert again == first
    assert list(tmp_path.iterdir()) == [first]


def test_ensure_extension_from_magic_and_content_type():
    from tender_agent.browser.downloads import ensure_extension

    pdf = b"%PDF-1.7\n..."
    assert ensure_extension("I. Общие сведения (товар)", pdf) == "I. Общие сведения (товар).pdf"
    assert ensure_extension("spec.pdf", pdf) == "spec.pdf"
    assert ensure_extension("file", b"xxx", "application/pdf") == "file.pdf"
    docx = b"PK\x03\x04" + b"\x00" * 30
    # без central directory zipfile упадёт → .zip; с word/ — .docx, проверяем только что суффикс появляется
    assert ensure_extension("без расширения", docx).endswith((".zip", ".docx"))


def test_sniff_old_office_by_utf16_stream_names():
    from tender_agent.browser.downloads import sniff_extension

    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
    assert sniff_extension(ole + "Workbook".encode("utf-16-le")) == ".xls"
    assert sniff_extension(ole + "WordDocument".encode("utf-16-le")) == ".doc"
    assert sniff_extension(ole + "PowerPoint Document".encode("utf-16-le")) == ".ppt"
    assert sniff_extension(ole) == ""


def test_browser_download_is_kept(tmp_path):
    import asyncio

    from tender_agent.browser.downloads import _store_download

    class FakeDownload:
        suggested_filename = "Проект контракта"

        async def save_as(self, path):
            with open(path, "wb") as f:
                f.write(b"%PDF-1.4 contract")

    first = asyncio.run(_store_download(FakeDownload(), tmp_path))
    again = asyncio.run(_store_download(FakeDownload(), tmp_path))
    assert first == again and first.name == "Проект контракта.pdf" and first.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["Проект контракта.pdf"]


def test_filename_from_encoded_url_and_long_names_keep_extension(tmp_path):
    from tender_agent.browser.downloads import _filename

    url = "https://44.tektorg.ru/file/get/t/AuctionDocs/id/1/name/%D0%9F%D1%80%D0%B8%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D0%B5%201.docx"
    assert _filename("", url) == "Приложение 1.docx"
    assert safe_destination(tmp_path, "а" * 200 + ".docx").name.endswith(".docx")
