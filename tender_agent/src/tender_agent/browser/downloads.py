from __future__ import annotations

import base64
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..api.schemas import DownloadRecord
from ..config import Settings
from ..sites.generic import FILE_RE
from ..tenders.models import Observation
from .dom_tools import element_by_ref
from .navigation import same_site, validate_navigation_url
from .runtime import BrowserRuntime


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


def _filename(disposition: str, url: str) -> str:
    matched = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?", disposition, re.I)
    if matched:
        return unquote((matched.group(1) or matched.group(2) or "").strip())
    return Path(urlparse(url).path).name or "file"


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
    host = urlparse(runtime.current_page.url).hostname or ""
    ok, reason = validate_navigation_url(source, settings)
    if not ok or not same_site(source, host):
        return _failed(source, tender_url, reason or "Файл ведёт на другой сайт.")
    try:
        loaded = await runtime.current_page.evaluate(
            """async (url) => {
              const response = await fetch(url);
              const bytes = new Uint8Array(await response.arrayBuffer());
              let binary = '';
              const step = 0x8000;
              for (let i = 0; i < bytes.length; i += step) {
                binary += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
              }
              return {
                ok: response.ok,
                status: response.status,
                url: response.url,
                header: response.headers.get('content-disposition') || '',
                b64: btoa(binary)
              };
            }""",
            source,
        )
    except Exception as exc:
        return _failed(source, tender_url, str(exc))
    final = str(loaded.get("url") or source)
    ok, reason = validate_navigation_url(final, settings)
    if not loaded.get("ok") or not ok or not same_site(final, host):
        return _failed(source, tender_url, reason or f"Загрузка отклонена, код {loaded.get('status')}.")
    try:
        body = base64.b64decode(str(loaded.get("b64") or ""))
    except Exception as exc:
        return _failed(source, tender_url, f"Ответ файла не прочитан: {exc}")
    if len(body) > settings.max_download_bytes:
        return _failed(source, tender_url, "Файл больше разрешённого размера.")
    target = safe_destination(runtime.downloads_dir, _filename(str(loaded.get("header") or ""), final))
    target.write_bytes(body)
    passed, detail = verify_download(target, runtime.downloads_dir, settings.max_download_bytes)
    if not passed:
        target.unlink(missing_ok=True)
        return _failed(source, tender_url, detail)
    return DownloadRecord(
        source_url=final,
        path=str(target),
        name=target.name,
        size=target.stat().st_size,
        status="saved",
        tender_url=tender_url,
    )


async def download_file(
    runtime: BrowserRuntime,
    obs: Observation,
    ref: int,
    settings: Settings,
    tender_url: str | None,
) -> DownloadRecord:
    element = element_by_ref(obs, ref)
    source = (element.href if element else "") or runtime.current_page.url
    if element and element.href and (element.download or FILE_RE.search(element.href) or FILE_RE.search(element.name or "")):
        return await _save_from_url(runtime, element.href, settings, tender_url)
    ok, reason = validate_navigation_url(source, settings)
    task_host = urlparse(runtime.current_page.url).hostname or ""
    if not ok or not same_site(source, task_host):
        return _failed(source, tender_url, reason or "Файл ведёт на другой сайт.")
    locator = runtime.current_page.locator(f'[data-ta-ref="{int(ref)}"]')
    target: Path | None = None
    try:
        async with runtime.current_page.expect_download(timeout=8_000) as pending:
            await locator.click(timeout=8_000)
        download = await pending.value
        target = safe_destination(runtime.downloads_dir, download.suggested_filename or "file")
        await download.save_as(str(target))
    except PlaywrightTimeout as exc:
        return _failed(source, tender_url, f"Браузер не начал загрузку: {exc}")
    except Exception as exc:
        if element and element.href:
            return await _save_from_url(runtime, element.href, settings, tender_url)
        return _failed(source, tender_url, str(exc))
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
