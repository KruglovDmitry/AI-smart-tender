"""Tool: download_url — skip duplicates / over limit."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .... import config
from ....core.browser import primitives as browser_tools
from ...context import PlatformAgentContext, ensure_tender_workspace, to_json, trace


class DownloadUrlInput(BaseModel):
    url: str = Field(description="Прямой URL файла (из list_download_links)")
    suggested_name: Optional[str] = Field(
        default=None,
        description="Осмысленное имя файла с расширением из текста ссылки",
    )


def _count_files_in_dir(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(
        1
        for p in folder.iterdir()
        if p.is_file() and p.name not in {"overview.json", "manifest.json"}
    )

def _existing_match(folder: Path, suggested_name: str | None) -> Path | None:
    if not folder.exists() or not suggested_name:
        return None
    want = Path(suggested_name).name.strip()
    if not want:
        return None
    stem = Path(want).stem.lower()
    for p in folder.iterdir():
        if not p.is_file() or p.name == "overview.json":
            continue
        if p.name == want or p.stem.lower() == stem:
            return p
    return None


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def download_url(url: str, suggested_name: str | None = None) -> str:
        args = {"url": url, "suggested_name": suggested_name}
        if not ctx.current_tender_id:
            # Browser-mode / без extract: взять id с текущего URL страницы
            from ....domain.tender_id import resolve_tender_id

            try:
                page_url = str(ctx.rt.page.url or "")
            except Exception:
                page_url = ""
            info = resolve_tender_id(page_url, platform=ctx.platform)
            tid = str(info.get("tender_id") or "").strip()
            if tid:
                ctx.current_tender_id = tid
                ctx.current_tender_url = page_url or ctx.current_tender_url
            else:
                result = {
                    "ok": False,
                    "action": "download_url",
                    "message": (
                        "Нет активного тендера: открой карточку (navigate) "
                        "или вызови extract_tender_id / save_tender_overview"
                    ),
                }
                trace(ctx, "download_url", args, result)
                return to_json(result)

        folder = ensure_tender_workspace(
            ctx, ctx.current_tender_id, ctx.current_tender_url
        )
        max_files = max(1, int(getattr(config, "PLATFORM_MAX_FILES_PER_TENDER", 5)))
        already = _count_files_in_dir(folder)
        if already >= max_files:
            result = {
                "ok": True,
                "action": "download_url",
                "skipped": True,
                "reason": "limit",
                "message": (
                    f"Лимит файлов на тендер ({max_files}) уже достигнут "
                    f"в {folder.name}; mark_tender_seen и следующий new."
                ),
                "tender_id": ctx.current_tender_id,
                "tender_dir": str(folder),
                "files_in_dir": already,
            }
            trace(ctx, "download_url", args, result)
            return to_json(result)

        if url in ctx.downloaded_urls:
            result = {
                "ok": True,
                "action": "download_url",
                "skipped": True,
                "reason": "duplicate_url",
                "message": f"URL уже скачивался в этом запуске: {suggested_name or url[:80]}",
                "tender_id": ctx.current_tender_id,
                "tender_dir": str(folder),
            }
            trace(ctx, "download_url", args, result)
            return to_json(result)

        existing = _existing_match(folder, suggested_name)
        if existing is not None:
            result = {
                "ok": True,
                "action": "download_url",
                "skipped": True,
                "reason": "exists",
                "message": f"Уже есть на диске: {existing.name}",
                "file": str(existing),
                "tender_id": ctx.current_tender_id,
                "tender_dir": str(folder),
            }
            ctx.downloaded_urls.add(url)
            trace(ctx, "download_url", args, result)
            return to_json(result)

        result = await browser_tools.download_url(ctx.rt, url, suggested_name)
        if isinstance(result, dict):
            result = {
                **result,
                "tender_id": ctx.current_tender_id,
                "tender_dir": str(folder),
            }
            if result.get("ok"):
                ctx.downloaded_urls.add(url)
                try:
                    from ....domain import manifest as manifest_mod

                    file_path = str(result.get("file") or "")
                    manifest_mod.append_file(
                        folder,
                        name=Path(file_path).name if file_path else (suggested_name or "document"),
                        sha256=str(result.get("sha256") or ""),
                        bytes_count=int(result.get("bytes") or 0),
                        source_url=str(result.get("source_url") or url),
                        content_type=str(result.get("content_type") or ""),
                        tender_id=str(ctx.current_tender_id or ""),
                        platform=ctx.platform,
                        tender_url=str(ctx.current_tender_url or ""),
                    )
                    result["manifest"] = str(manifest_mod.manifest_path(folder))
                except Exception as e:
                    result["manifest_error"] = str(e)[:200]
        trace(ctx, "download_url", args, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=download_url,
        name="download_url",
        description=(
            "Скачать файл по прямому URL в папку текущего тендера "
            "(tenders/<platform>/<tender_id>/).\n"
            f"Лимит: до {getattr(config, 'PLATFORM_MAX_FILES_PER_TENDER', 5)} файлов на тендер. "
            "Авто-skip: уже скачанный URL или такое же имя на диске.\n"
            "КОГДА: после save_tender_overview и list_download_links.\n"
            "ВЕРНЁТ JSON: ok, skipped?, file, tender_dir, bytes."
        ),
        args_schema=DownloadUrlInput,
    )
