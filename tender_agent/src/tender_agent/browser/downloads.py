from __future__ import annotations

import base64
import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..api.schemas import DownloadRecord
from ..config import Settings
from ..tenders.models import Observation
from .dom_tools import element_by_ref
from .navigation import validate_navigation_url
from .runtime import BrowserRuntime

_FETCH_JS = """async (url) => {
  const response = await fetch(url);
  const bytes = new Uint8Array(await response.arrayBuffer());
  let binary = '';
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
  }
  return {
    status: response.status,
    url: response.url,
    header: response.headers.get('content-disposition') || '',
    type: response.headers.get('content-type') || '',
    b64: btoa(binary)
  };
}"""


def safe_destination(root: Path, name: str) -> Path:
    cleaned = re.sub(r"[^\w.\- ]+", "_", name, flags=re.UNICODE).strip(" .") or "file"
    cleaned = cleaned[:140]
    root.mkdir(parents=True, exist_ok=True)
    dest = (root / cleaned).resolve()
    if not dest.is_relative_to(root.resolve()):
        raise RuntimeError("Путь загрузки выходит за рабочую директорию.")
    return dest


def verify_download(path: Path, root: Path, max_bytes: int) -> tuple[bool, str]:
    if not path.is_file():
        return False, "Файл не появился на диске."
    if not path.resolve().is_relative_to(root.resolve()):
        return False, "Файл записан вне рабочей директории."
    size = path.stat().st_size
    if size <= 0:
        return False, "Файл пустой."
    if size > max_bytes:
        return False, "Файл больше разрешённого размера."
    if path.suffix.lower() == ".pdf":
        head = path.read_bytes()[:5]
        if not head.startswith(b"%PDF-"):
            return False, "Содержимое не похоже на PDF."
    return True, ""


def fix_mojibake(name: str) -> str:
    """Имя в UTF-8, прочитанное как latin-1 (ЕИС 223-ФЗ: «Ð_Ð¾ÐºÑ…» вместо «Документ…»)."""
    try:
        fixed = name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name
    return fixed if fixed != name else name


def _filename(disposition: str, url: str) -> str:
    # filename* (RFC 5987) точнее: Росэлторг кладёт в filename «____ ____.docx», а настоящее имя — в filename*.
    encoded = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", disposition, re.I)
    if encoded:
        return unquote(encoded.group(1).strip().strip('"'))
    matched = re.search(r"filename\s*=\s*\"?([^\";]+)\"?", disposition, re.I)
    if matched:
        return fix_mojibake(unquote(matched.group(1).strip()))
    return fix_mojibake(Path(urlparse(url).path).name) or "file"


def _failed(source: str, tender_url: str | None, detail: str) -> DownloadRecord:
    return DownloadRecord(
        source_url=source,
        path="",
        name="",
        size=0,
        status="failed",
        tender_url=tender_url,
        detail=detail,
    )


async def _save_from_url(
    runtime: BrowserRuntime,
    source: str,
    settings: Settings,
    tender_url: str | None,
) -> DownloadRecord:
    # Файл качаем оттуда, куда ведёт ссылка, даже с другого сайта: площадки держат документы
    # в хранилище ЕИС (Сбер-АСТ → zakupki.gov.ru/.../filestore). Запрос идёт через контекст браузера
    # (те же cookie, без ограничений CORS). Внутренние и локальные адреса по-прежнему запрещены.
    ok, reason = validate_navigation_url(source, settings)
    if not ok:
        return _failed(source, tender_url, reason)
    try:
        if urlparse(source).netloc == urlparse(runtime.current_page.url).netloc:
            # Тот же адрес (хост и порт) — из страницы, с её сессией. Любой другой адрес, даже поддомен
            # (business.roseltorg.ru со страницы www.roseltorg.ru), — запросом браузера: запрос из страницы
            # браузер блокирует по CORS.
            loaded = await runtime.current_page.evaluate(_FETCH_JS, source)
            status, final = int(loaded.get("status") or 0), str(loaded.get("url") or source)
            headers = {"content-disposition": str(loaded.get("header") or ""), "content-type": str(loaded.get("type") or "")}
            body = base64.b64decode(str(loaded.get("b64") or ""))
        else:
            # ЕИС подписана сертификатом российского УЦ, которого нет в хранилище Node — браузер его
            # принимает, запрос Playwright нет. Для скачивания документа проверку цепочки не требуем.
            response = await runtime.context.request.get(source, timeout=60_000, max_redirects=5,
                                                         ignore_https_errors=True)
            status, final, headers, body = response.status, response.url or source, response.headers, await response.body()
    except Exception as exc:
        return _failed(source, tender_url, str(exc).splitlines()[0][:300])
    ok, reason = validate_navigation_url(final, settings)
    if not 200 <= status < 300 or not ok:
        return _failed(source, tender_url, reason or f"Загрузка отклонена, код {status}.")
    header = headers.get("content-disposition", "")
    if "text/html" in headers.get("content-type", "") and "attachment" not in header.lower():
        return _failed(source, tender_url, "По ссылке страница, а не файл.")
    if len(body) > settings.max_download_bytes:
        return _failed(source, tender_url, "Файл больше разрешённого размера.")
    target = safe_destination(runtime.downloads_dir, _filename(header, final))
    target.write_bytes(body)
    passed, detail = verify_download(target, runtime.downloads_dir, settings.max_download_bytes)
    if not passed:
        target.unlink(missing_ok=True)
        return _failed(source, tender_url, detail)
    return DownloadRecord(
        source_url=source,
        final_url=final if final != source else None,
        path=str(target),
        name=target.name,
        size=target.stat().st_size,
        status="saved",
        tender_url=tender_url,
    )


# Без \b: регулярка уходит в браузер, а там \b не понимает кириллицу.
_DOWNLOAD_BUTTON = re.compile(r"^\s*(скачать|загрузить|download)(?![а-яёa-z])", re.I)


async def _dialog_button(page):
    """Видимая кнопка «Скачать», появившаяся после клика по файлу; None — такой нет."""
    buttons = page.locator("a, button, [role=button], input[type=button], input[type=submit]").filter(
        has_text=_DOWNLOAD_BUTTON)
    for index in reversed(range(await buttons.count())):  # окно обычно в конце документа
        button = buttons.nth(index)
        if await button.is_visible():
            return button
    return None


async def download_file(
    runtime: BrowserRuntime,
    obs: Observation,
    ref: int,
    settings: Settings,
    tender_url: str | None,
) -> DownloadRecord:
    element = element_by_ref(obs, ref)
    source = (element.href if element else "") or runtime.current_page.url
    if element and element.href:
        # Ссылка — качаем по адресу (filestore ЕИС без расширения тоже); если там страница, а не файл, —
        # пробуем кликом, как пользователь.
        saved = await _save_from_url(runtime, element.href, settings, tender_url)
        if saved.status == "saved" or saved.detail != "По ссылке страница, а не файл.":
            return saved
    ok, reason = validate_navigation_url(source, settings)
    if not ok:
        return _failed(source, tender_url, reason)
    page = runtime.current_page
    locator = page.locator(f'[data-ta-ref="{int(ref)}"]')
    target: Path | None = None
    # Загрузка может начаться в этой вкладке или во всплывающей (РТС-тендер открывает documentLink
    # в новой вкладке, а там окно с кнопкой «Скачать») — слушаем обе. Чужие вкладки (другие карточки
    # читаются параллельно) не трогаем: только всплывающие окна этой страницы.
    downloads: list = []
    popups: list = []

    def on_download(download) -> None:
        downloads.append(download)

    def on_popup(new_page) -> None:
        popups.append(new_page)
        new_page.on("download", on_download)

    page.on("download", on_download)
    page.on("popup", on_popup)

    async def wait_download(where, seconds: float) -> bool:
        for _ in range(int(seconds * 4)):
            if downloads:
                return True
            await where.wait_for_timeout(250)
        return bool(downloads)

    try:
        await locator.click(timeout=8_000)
        if not await wait_download(page, 6):
            dialog_page = popups[-1] if popups else page
            button = None
            for _ in range(40):  # до 10 с: новая вкладка может сначала пройти Anti-DDoS
                button = await _dialog_button(dialog_page)
                if button is not None or downloads:
                    break
                await dialog_page.wait_for_timeout(250)
            if not downloads:
                if button is None:
                    return _failed(source, tender_url, "Браузер не начал загрузку, кнопки «Скачать» после клика нет.")
                href = await button.get_attribute("href")
                if href and not href.lower().startswith("javascript:"):
                    return await _save_from_url(runtime, urljoin(dialog_page.url, href), settings, tender_url)
                await button.click(timeout=8_000)
                if not await wait_download(dialog_page, 15):
                    return _failed(source, tender_url, "После «Скачать» загрузка не началась.")
        download = downloads[0]
        target = safe_destination(runtime.downloads_dir, fix_mojibake(download.suggested_filename or "file"))
        await download.save_as(str(target))
    except PlaywrightTimeout as exc:
        return _failed(source, tender_url, f"Браузер не начал загрузку: {str(exc).splitlines()[0][:200]}")
    except Exception as exc:
        return _failed(source, tender_url, str(exc).splitlines()[0][:300])
    finally:
        page.remove_listener("download", on_download)
        page.remove_listener("popup", on_popup)
        for popup in popups:
            if not popup.is_closed():
                await popup.close()
        if not popups and not page.is_closed():
            await page.keyboard.press("Escape")  # закрыть окно «Скачать», если оно было в этой вкладке
    passed, detail = verify_download(target, runtime.downloads_dir, settings.max_download_bytes)
    if not passed and target.is_file():
        target.unlink(missing_ok=True)
    size = target.stat().st_size if target.is_file() else 0
    return DownloadRecord(
        source_url=source,
        path=str(target) if passed else "",
        name=target.name if passed else "",
        size=size if passed else 0,
        status="saved" if passed else "failed",
        tender_url=tender_url,
        detail=detail,
    )
