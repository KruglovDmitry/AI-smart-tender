"""Browser primitive tools — agent chooses DOM vs screenshot path itself."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from ... import config
from .page_kind import detect_page_kind
from .session import BrowserRuntime

logger = logging.getLogger(__name__)

DOC_EXT_RE = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|csv|txt|rtf)(\?|$)",
    re.I,
)
DOC_HINT_RE = re.compile(
    r"(скачать|download|документ|файл|вложен|attach|documentation|спецификац)",
    re.I,
)
DOC_PRIORITY_RE = re.compile(
    r"контракт|договор|описан|технич|заявк|нмцк|обоснован|спецификац|"
    r"тз\b|requirement|specification|proposal|contract|annex|приложен",
    re.I,
)
# Generic noise (not platform-specific): analytics/traffic/legal chrome
NOISE_LINK_RE = re.compile(
    r"traffic|analytics|visit.?count|посещаем|cookie|mailto:|javascript:|"
    r"user.?agreement|privacy|подписк|\brss\b|/rpt/",
    re.I,
)


async def _settle(page, short: bool = True) -> None:
    timeout = 8_000 if short else 20_000
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass


def _ok(action: str, message: str, **data: Any) -> dict[str, Any]:
    return {"ok": True, "action": action, "message": message, **data}


def _err(action: str, message: str, **data: Any) -> dict[str, Any]:
    return {"ok": False, "action": action, "message": message, **data}


def _clamp_xy(rt: BrowserRuntime, x: float, y: float) -> tuple[float, float, bool]:
    """Держим клики внутри viewport (агент иногда выдаёт x>width)."""
    vp = rt.page.viewport_size or {}
    w = float(vp.get("width") or config.BROWSER_VIEWPORT_WIDTH)
    h = float(vp.get("height") or config.BROWSER_VIEWPORT_HEIGHT)
    cx = max(0.0, min(float(x), w - 1.0))
    cy = max(0.0, min(float(y), h - 1.0))
    return cx, cy, (cx != float(x) or cy != float(y))


async def navigate(rt: BrowserRuntime, url: str) -> dict[str, Any]:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _err("navigate", "URL must be http(s) with host")
    try:
        resp = await rt.page.goto(url, wait_until="domcontentloaded")
        await _settle(rt.page, short=False)
        status = getattr(resp, "status", None) if resp is not None else None
        kind_info = await detect_page_kind(rt)
        title = kind_info.get("title") or ""
        not_found = status == 404 or kind_info.get("page_kind") == "not_found"
        if not_found:
            return _err(
                "navigate",
                "Страница не найдена (404 / page_kind=not_found). "
                "Не выдумывай URL — вернись на выдачу и navigate по exact href.",
                url=rt.page.url,
                title=title,
                http_status=status,
                not_found=True,
                page_kind="not_found",
                page_kind_reason=kind_info.get("page_kind_reason"),
            )
        return _ok(
            "navigate",
            f"Opened {rt.page.url} (page_kind={kind_info.get('page_kind')})",
            url=rt.page.url,
            title=title,
            http_status=status,
            **{k: v for k, v in kind_info.items() if k not in {"url", "title"}},
        )
    except Exception as e:
        return _err("navigate", str(e), url=rt.page.url)


async def screenshot(rt: BrowserRuntime) -> dict[str, Any]:
    try:
        await rt.page.set_viewport_size(
            {
                "width": rt.page.viewport_size["width"]
                if rt.page.viewport_size
                else 1280,
                "height": rt.page.viewport_size["height"]
                if rt.page.viewport_size
                else 900,
            }
        )
    except Exception:
        pass
    try:
        png = await rt.page.screenshot(type="png", full_page=False)
        b64 = base64.b64encode(png).decode("ascii")
        meta = {
            "width": (rt.page.viewport_size or {}).get("width", 1280),
            "height": (rt.page.viewport_size or {}).get("height", 900),
            "url": rt.page.url,
            "title": await rt.page.title(),
        }
        rt.last_screenshot_b64 = b64
        rt.last_screenshot_meta = meta
        return _ok(
            "screenshot",
            "Screenshot captured. Coordinates are in viewport pixels "
            f"({meta['width']}x{meta['height']}). Image attached for vision.",
            **meta,
            has_image=True,
        )
    except Exception as e:
        return _err("screenshot", str(e))


async def click_xy(
    rt: BrowserRuntime,
    x: float,
    y: float,
    expect_download: bool = False,
) -> dict[str, Any]:
    """Coordinate click — fallback when DOM-by-id is unavailable."""
    page = rt.page
    try:
        url_before = page.url
        title_before = ""
        try:
            title_before = await page.title()
        except Exception:
            pass
        x, y, clamped = _clamp_xy(rt, x, y)
        clamp_note = f" (clamped to viewport)" if clamped else ""
        if expect_download:
            async with page.expect_download(timeout=30_000) as dl_info:
                await page.mouse.click(x, y)
            download = await dl_info.value
            fname = download.suggested_filename or "download.bin"
            dest = rt.downloads_dir / fname
            await download.save_as(str(dest))
            rel = str(dest)
            rt.downloaded_files.append(rel)
            await _settle(page)
            kind_info = await detect_page_kind(rt)
            return _ok(
                "click_xy",
                f"Clicked ({x:.0f},{y:.0f}){clamp_note} and downloaded {fname}",
                x=x,
                y=y,
                clamped=clamped,
                file=rel,
                url=page.url,
                changed=True,
                page_kind=kind_info.get("page_kind"),
            )

        ctx = page.context
        pages_before = list(ctx.pages)
        await page.mouse.click(x, y)
        await page.wait_for_timeout(400)
        # adopt new tab if opened
        fresh = [p for p in ctx.pages if p not in pages_before and not p.is_closed()]
        if not fresh:
            fresh = [
                p
                for p in ctx.pages
                if not p.is_closed() and id(p) not in rt.known_pages
            ]
        if fresh:
            new_page = fresh[-1]
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=30_000)
            except Exception:
                pass
            rt.adopt(new_page)
            await _settle(rt.page)
            kind_info = await detect_page_kind(rt)
            return _ok(
                "click_xy",
                f"Clicked ({x:.0f},{y:.0f}){clamp_note} -> new tab",
                x=x,
                y=y,
                clamped=clamped,
                url=rt.page.url,
                new_tab=True,
                changed=True,
                page_kind=kind_info.get("page_kind"),
            )

        await _settle(page)
        kind_info = await detect_page_kind(rt)
        url_after = page.url
        title_after = kind_info.get("title") or ""
        changed = (url_after != url_before) or (title_after != title_before)
        return _ok(
            "click_xy",
            f"Clicked ({x:.0f},{y:.0f}){clamp_note}"
            + ("" if changed else " (URL/title unchanged — possible miss)"),
            x=x,
            y=y,
            clamped=clamped,
            url=url_after,
            changed=changed,
            page_kind=kind_info.get("page_kind"),
        )
    except Exception as e:
        return _err("click_xy", str(e), x=x, y=y, url=page.url)


async def type_text(
    rt: BrowserRuntime,
    text: str,
    x: float | None = None,
    y: float | None = None,
    clear: bool = True,
) -> dict[str, Any]:
    page = rt.page
    try:
        clamped = False
        if x is not None and y is not None:
            x, y, clamped = _clamp_xy(rt, x, y)
            await page.mouse.click(x, y)
            await page.wait_for_timeout(150)
        if clear:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
        await page.keyboard.type(str(text), delay=25)
        msg = f"Typed {len(text)} chars"
        if clamped:
            msg += f" (click clamped to {x:.0f},{y:.0f})"
        return _ok(
            "type_text",
            msg,
            text=text,
            x=x,
            y=y,
            clamped=clamped,
            url=page.url,
        )
    except Exception as e:
        return _err("type_text", str(e))


async def press_key(rt: BrowserRuntime, key: str = "Enter") -> dict[str, Any]:
    page = rt.page
    try:
        await page.keyboard.press(key)
        await _settle(page)
        return _ok("press_key", f"Pressed {key}", key=key, url=page.url)
    except Exception as e:
        return _err("press_key", str(e))


async def scroll(rt: BrowserRuntime, delta_y: int = 600, times: int = 1) -> dict[str, Any]:
    page = rt.page
    try:
        times = max(1, int(times))
        for _ in range(times):
            await page.evaluate(f"() => window.scrollBy(0, {int(delta_y)})")
            await page.wait_for_timeout(200)
        return _ok(
            "scroll",
            f"Scrolled dy={delta_y} x{times}",
            delta_y=delta_y,
            times=times,
            url=page.url,
        )
    except Exception as e:
        return _err("scroll", str(e))


async def wait(rt: BrowserRuntime, seconds: float = 1.0) -> dict[str, Any]:
    ms = int(max(0.0, float(seconds)) * 1000)
    await rt.page.wait_for_timeout(ms)
    return _ok("wait", f"Waited {seconds}s", url=rt.page.url)


async def get_page_text(rt: BrowserRuntime, max_chars: int = 12000) -> dict[str, Any]:
    page = rt.page
    try:
        text = await page.evaluate(
            """() => {
              const clone = document.body.cloneNode(true);
              for (const sel of ['script','style','noscript','svg']) {
                clone.querySelectorAll(sel).forEach(n => n.remove());
              }
              return (clone.innerText || '').replace(/\\s+/g, ' ').trim();
            }"""
        )
        text = (text or "")[: max(1000, max_chars)]
        return _ok(
            "get_page_text",
            "Extracted visible page text",
            url=page.url,
            title=await page.title(),
            chars=len(text),
            text=text,
        )
    except Exception as e:
        return _err("get_page_text", str(e))


async def list_download_links(rt: BrowserRuntime, limit: int = 40) -> dict[str, Any]:
    """DOM scan for likely document links — cheap path without vision."""
    page = rt.page
    try:
        raw = await page.evaluate(
            """() => {
              const out = [];
              const norm = (v) => String(v ?? '')
                .replace(/\\s+/g, ' ')
                .trim()
                .slice(0, 160);
              const push = (href, text, tag) => {
                const h = norm(href);
                // Keep text-only controls (tabs/buttons) even without href.
                if (!h && !norm(text)) return;
                out.push({
                  href: h,
                  text: norm(text),
                  tag: tag || 'a'
                });
              };
              for (const a of document.querySelectorAll('a[href]')) {
                push(
                  a.href,
                  a.innerText || a.textContent || a.getAttribute('title') || a.href,
                  'a'
                );
              }
              for (const el of document.querySelectorAll(
                'button, [role="button"], input[type="button"], input[type="submit"], [role="tab"], .tab, .nav-link'
              )) {
                const t =
                  el.innerText ||
                  el.textContent ||
                  el.value ||
                  el.getAttribute('aria-label') ||
                  '';
                push(el.getAttribute('href') || el.href || '', t, el.tagName.toLowerCase());
              }
              return out.slice(0, 200);
            }"""
        )
        base = page.url
        links: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw or []:
            href = (item.get("href") or "").strip()
            text = item.get("text") or ""
            if href:
                href = urljoin(base, href)
            score = 0
            if href and DOC_EXT_RE.search(href):
                score += 4
            if DOC_HINT_RE.search(text) or (href and DOC_HINT_RE.search(href)):
                score += 2
            if DOC_PRIORITY_RE.search(text):
                score += 3
            if not href and DOC_HINT_RE.search(text):
                score += 1
            blob = f"{href} {text}"
            if NOISE_LINK_RE.search(blob):
                score -= 10
            if score <= 0:
                continue
            key = href or f"text:{text}"
            if key in seen:
                continue
            seen.add(key)
            kind = "file" if (href and (DOC_EXT_RE.search(href) or "download" in href.lower() or "filestore" in href.lower() or "uid=" in href.lower())) else "other"
            if NOISE_LINK_RE.search(blob):
                kind = "noise"
            links.append(
                {
                    "href": href or None,
                    "text": text,
                    "tag": item.get("tag"),
                    "score": score,
                    "kind": kind,
                }
            )
        links.sort(key=lambda x: (-x["score"], x.get("text") or ""))
        links = links[: max(1, limit)]
        return _ok(
            "list_download_links",
            f"Found {len(links)} candidate download/document controls",
            url=page.url,
            links=links,
        )
    except Exception as e:
        return _err("list_download_links", str(e))


def _filename_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    from urllib.parse import unquote_to_bytes

    # filename*=UTF-8''...  or filename*=windows-1251''...
    m = re.search(
        r"filename\*\s*=\s*([^']+)''([^;]+)",
        header,
        flags=re.I,
    )
    if m:
        charset = (m.group(1) or "utf-8").strip().lower().replace("utf8", "utf-8")
        raw = m.group(2).strip().strip('"')
        try:
            data = unquote_to_bytes(raw)
            if charset == "utf-8":
                return _fix_filename_encoding(data.decode("utf-8", errors="replace"))
            return _fix_filename_encoding(data.decode(charset, errors="replace"))
        except Exception:
            pass

    m = re.search(r'filename\s*=\s*"([^"]+)"', header, flags=re.I)
    if m:
        return _fix_filename_encoding(m.group(1).strip())
    m = re.search(r"filename\s*=\s*([^;]+)", header, flags=re.I)
    if m:
        return _fix_filename_encoding(m.group(1).strip().strip('"'))
    return None


def _fix_filename_encoding(name: str) -> str:
    """Recover Cyrillic from Latin-1-misdecoded UTF-8 / cp1251 headers."""
    name = (name or "").strip()
    if not name:
        return name
    if re.search(r"[а-яА-ЯёЁ]", name):
        return name
    for enc in ("utf-8", "cp1251"):
        try:
            fixed = name.encode("latin-1").decode(enc)
        except Exception:
            continue
        if re.search(r"[а-яА-ЯёЁ]", fixed):
            return fixed
    return name


def _safe_filename(name: str) -> str:
    name = _fix_filename_encoding(name)
    name = name.replace("\\", "_").replace("/", "_").replace("\x00", "")
    name = re.sub(r"[\x00-\x1f]", "", name)
    # Keep letters/digits (incl. Cyrillic), common punctuation used by EIS
    name = re.sub(
        r"[^\w.\-()+\sа-яА-ЯёЁ№«»—–]",
        "_",
        name,
        flags=re.UNICODE,
    )
    name = re.sub(r"_+", "_", name).strip(" ._")
    return name[:180] or "download.bin"


def _unique_dest(directory: Path, name: str) -> Path:
    dest = directory / name
    if not dest.exists():
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    for i in range(2, 200):
        candidate = directory / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}_dup{suffix}"

def download_result_meta(body: bytes, content_type: str = "") -> dict[str, Any]:
    """Pure metadata for a downloaded body (bytes, sha256, content_type)."""
    return {
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "content_type": (content_type or "").split(";")[0].strip(),
    }


def _guess_ext_from_bytes(body: bytes, content_type: str = "") -> str:
    ct = (content_type or "").lower()
    if "pdf" in ct or body[:4] == b"%PDF":
        return ".pdf"
    if body[:2] == b"PK":
        # Distinguish Office Open XML by zip members
        try:
            import io
            import zipfile

            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                names = {n.lower() for n in zf.namelist()}
            if any(n.startswith("word/") for n in names):
                return ".docx"
            if any(n.startswith("xl/") for n in names):
                return ".xlsx"
            if any(n.startswith("ppt/") for n in names):
                return ".pptx"
        except Exception:
            pass
        return ".docx"
    if body[:4] == b"\xd0\xcf\x11\xe0":  # OLE compound
        head = body[:8192]
        if b"Workbook" in head or b"Book\x00" in head or "excel" in ct or "sheet" in ct:
            return ".xls"
        if "word" in ct or b"WordDocument" in head:
            return ".doc"
        return ".doc"
    if body[:2] == b"\x1f\x8b":
        return ".gz"
    if "msword" in ct:
        return ".doc"
    if "excel" in ct or "spreadsheet" in ct:
        return ".xls"
    return ""


async def download_url(
    rt: BrowserRuntime,
    url: str,
    suggested_name: str | None = None,
) -> dict[str, Any]:
    """Download a direct file URL into downloads_dir."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return _err("download_url", "Only http(s) URLs allowed")
    try:
        # Prefer browser context cookies/auth
        resp = await rt.page.request.get(url, timeout=120_000)
        if not resp.ok:
            return _err("download_url", f"HTTP {resp.status}", url=url)
        body = await resp.body()
        headers = {k.lower(): v for k, v in (resp.headers or {}).items()}
        content_type = headers.get("content-type") or ""
        name = _filename_from_content_disposition(headers.get("content-disposition"))
        # Prefer readable page-link text when Content-Disposition is mojibake
        hint = _fix_filename_encoding((suggested_name or "").strip())
        if hint and re.search(r"[а-яА-ЯёЁ]", hint):
            name = hint
        elif not name or not re.search(r"[а-яА-ЯёЁ]", name or ""):
            name = hint or name or Path(parsed.path).name or "download.bin"
        name = _fix_filename_encoding(name)
        # EIS often serves real office/pdf via .../file.html?uid=...
        guessed = _guess_ext_from_bytes(body, content_type)
        suffix = Path(name).suffix.lower()
        if (
            not suffix
            or suffix in {".html", ".htm", ".bin", ".php", ".aspx"}
            or name.lower() in {"file", "download", "download.bin", "file.html"}
        ):
            if guessed:
                stem = Path(name).stem if name else "document"
                if stem.lower() in {"file", "download", "download.bin", "file.html", ""}:
                    stem = "document"
                name = f"{stem}{guessed}"
        elif guessed and suffix != guessed and suffix in {".html", ".htm"}:
            name = f"{Path(name).stem}{guessed}"

        name = _safe_filename(name)
        dest = _unique_dest(rt.downloads_dir, name)
        dest.write_bytes(body)
        rt.downloaded_files.append(str(dest))
        meta = download_result_meta(body, content_type)
        return _ok(
            "download_url",
            f"Saved {dest.name} ({meta['bytes']} bytes)",
            file=str(dest),
            bytes=meta["bytes"],
            sha256=meta["sha256"],
            content_type=meta["content_type"],
            url=url,
            source_url=url,
        )
    except Exception as e:
        return _err("download_url", str(e), url=url)


async def finish(
    rt: BrowserRuntime,
    summary: str,
    success: bool = True,
) -> dict[str, Any]:
    return {
        "ok": True,
        "action": "finish",
        "message": summary,
        "success": success,
        "url": rt.page.url,
        "title": await rt.page.title(),
        "downloaded_files": list(rt.downloaded_files),
        "done": True,
    }


TOOL_HANDLERS = {
    "navigate": navigate,
    "screenshot": screenshot,
    "click_xy": click_xy,
    "type_text": type_text,
    "press_key": press_key,
    "scroll": scroll,
    "wait": wait,
    "get_page_text": get_page_text,
    "list_download_links": list_download_links,
    "download_url": download_url,
    "finish": finish,
}
