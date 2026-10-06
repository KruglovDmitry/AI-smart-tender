"""Rosatom AtomForm tools + API client (zakupki.rosatom.ru).

Structured tools for the agent. EIS keeps open_platform_search / list_new_cards.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..context import PlatformAgentContext, ensure_tender_workspace, to_json, trace

# --- AtomForm client ---------------------------------------------------------

ATOMFORM_PATH = "/af/atomformapi"
LIST_FORM_ID = "РегистрСведений.ГА_ЖурналЗакупокНаСайте.Форма.КОД_ФормаСписка"
HOST = "https://zakupki.rosatom.ru"
CARD_FORM_ID = "Документ.ГА_ЗакупочнаяПроцедура.Форма.КОД_ФормаДокумента"

_RT_TOKEN = "_rosatom_atomform_token"
_RT_JWT = "_rosatom_atomform_jwt"
_RT_SESSION = "_rosatom_atomform_session"
_RT_ROWS = "_rosatom_atomform_rows"
_RT_CARD = "_rosatom_atomform_card"


def present(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("Представление", "Значение", "value"):
            if value.get(key) not in (None, ""):
                return str(value.get(key)).strip() or None
        return None
    text = str(value).strip()
    return text or None


def card_url(number: str | None, uuid: str | None) -> str:
    q: dict[str, str] = {"link": "procurements"}
    if number:
        q["number"] = str(number)
    if uuid:
        q["procId"] = str(uuid)
    return f"{HOST}/?{urlencode(q)}"


def parse_card_url(url: str) -> tuple[str | None, str | None]:
    qs = parse_qs(urlparse(url or "").query)
    number = (qs.get("number") or [None])[0]
    proc = (qs.get("procId") or qs.get("id") or [None])[0]
    return (
        str(number).strip() if number else None,
        str(proc).strip() if proc else None,
    )


def rows_from_getlink(payload: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        actions = payload["body"]["response"]["Действия"]
        data = actions[0]["Данные"]
        rows = data.get("ГА_ЖурналЗакупокНаСайте") or []
    except Exception:
        return []
    return [r for r in rows if isinstance(r, dict)]


def card_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    number = present(row.get("Номер"))
    link = row.get("с_Ссылка") if isinstance(row.get("с_Ссылка"), dict) else {}
    uuid = str(link.get("Ид") or "").strip() or None
    if not number and not uuid:
        return None
    title = present(row.get("ПредметДоговораЗПРус")) or present(link.get("Представление"))
    return {
        "url": card_url(number, uuid),
        "title": title,
        "tender_id": number or uuid,
        "law": "223",
    }


def list_summary(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        data = payload["body"]["response"]["Действия"][0]["Данные"]
        pages = (data.get("с_ПараметрыСписков") or {}).get("ГА_ЖурналЗакупокНаСайте") or {}
    except Exception:
        pages = {}
    return {
        "total_rows": pages.get("ВсегоСтрок"),
        "page_size": pages.get("СтрокНаСтранице"),
        "total_pages": pages.get("ВсегоСтраниц"),
    }


def overview_from_card(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        a0 = payload["body"]["response"]["Действия"][0]
        data = a0.get("Данные") or {}
    except Exception:
        return {}
    title = a0.get("ЗаголовокОкна")
    fields = {
        "number": present(data.get("АктивнаяВерсия.НомерЗПНаСайтеЗакупокГК")),
        "nmck": present(data.get("АктивнаяВерсия.НМЦЛотов")),
        "published": present(data.get("АктивнаяВерсия.ДатаПубликацииГК")),
        "deadline": present(data.get("АктивнаяВерсия.ДатаОкончанияСрокаПодачиЗаявок")),
        "method": present(data.get("АктивнаяВерсия.СпособЗакупки")),
        "organizer": present(data.get("АктивнаяВерсия.ОрганизаторЗакупки")),
        "status": present(data.get("АктивнаяВерсия.СтатусЗакупочнойПроцедуры")),
        "eis_number": present(data.get("АктивнаяВерсия.НомерЗакупкиНаЕИС")),
    }
    lots = []
    for row in data.get("ТаблицаПозиций") or []:
        if not isinstance(row, dict):
            continue
        lots.append(
            {
                "name": present(row.get("ТаблицаПозиций.НаименованиеЛота")),
                "nmck": present(row.get("ТаблицаПозиций.НМЦ")),
                "status": present(row.get("ТаблицаПозиций.Статус")),
            }
        )
    return {
        "window_title": title,
        "fields": {k: v for k, v in fields.items() if v},
        "lots": lots,
        "files_count": len(data.get("ТаблицаФайлов") or []),
        "notice_files_count": len(data.get("ТаблицаФайловИзвещения") or []),
    }


def docs_from_card(payload: dict[str, Any]) -> list[dict[str, str]]:
    try:
        data = payload["body"]["response"]["Действия"][0]["Данные"]
    except Exception:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for table, name_key, link_key, type_key in (
        (
            "ТаблицаФайлов",
            "ТаблицаФайлов.НаименованиеФайла",
            "ТаблицаФайлов.Ссылка",
            "ТаблицаФайлов.ТипФайла",
        ),
        (
            "ТаблицаФайловИзвещения",
            "ТаблицаФайловИзвещения.НаименованиеФайла",
            "ТаблицаФайловИзвещения.Ссылка",
            "ТаблицаФайловИзвещения.ТипФайла",
        ),
    ):
        for row in data.get(table) or []:
            if not isinstance(row, dict):
                continue
            link = row.get(link_key) if isinstance(row.get(link_key), dict) else {}
            file_id = str(link.get("Ид") or "").strip()
            name = present(row.get(name_key)) or present(link.get("Представление")) or "file"
            if not file_id or file_id in seen:
                continue
            seen.add(file_id)
            kind = present(row.get(type_key)) or "file"
            out.append(
                {
                    "url": f"{HOST}/?rosatomFileId={file_id}&name={name}",
                    "name": name,
                    "kind": str(kind),
                }
            )
    return out


def _store_token(rt: Any, token: str | None, session: str | None = None) -> None:
    if token:
        setattr(rt, _RT_TOKEN, token)
        # SSE accessToken is a fuller JWT; prefer it for /af/storage.
        if token.startswith("eyJ") and len(token) > 80:
            setattr(rt, _RT_JWT, token)
    if session:
        setattr(rt, _RT_SESSION, session)


def _auth_token(rt: Any) -> str | None:
    return getattr(rt, _RT_JWT, None) or getattr(rt, _RT_TOKEN, None)


def _safe_filename(name: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "file").strip()) or "file"
    return text[:180]


def card_form_context(card_payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        data = card_payload["body"]["response"]["Действия"][0]["Данные"]
    except Exception:
        return None
    obj_ref = data.get("Объект.Ссылка")
    if not isinstance(obj_ref, dict):
        return None
    return {
        "_GetElementContext": {
            "ВерсияДанных": "AAAAAAAAAAI=",
            "ИмяФормы": CARD_FORM_ID,
            "Ссылка": obj_ref,
            "ТипФормы": {
                "Вид": "ГА_ТипыФорм",
                "Ид": "ПросмотрОбъекта",
                "Представление": "Просмотр объекта",
                "Тип": "Перечисление",
            },
        },
        "АктивнаяВерсия.Ссылка": data.get("АктивнаяВерсия.Ссылка"),
        "АктивнаяВерсия.СпособЗакупки": data.get("АктивнаяВерсия.СпособЗакупки"),
        "Объект.Ссылка": obj_ref,
        "ТаблицаФайлов": data.get("ТаблицаФайлов") or [],
    }


def minio_from_payload(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    try:
        data = payload["body"]["response"]["Действия"][0]["Данные"]
    except Exception:
        return None, None
    name = data.get("НС_ИмяФайлаМинио")
    path = data.get("НС_ПутьФайлаМинио")
    return (
        str(name).strip() if name else None,
        str(path).strip() if path else None,
    )


def _store_rows(rt: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = getattr(rt, _RT_ROWS, {}) or {}
    refs: list[dict[str, Any]] = []
    for row in rows:
        ref = card_from_row(row)
        if not ref:
            continue
        refs.append(ref)
        if ref.get("tender_id"):
            cache[str(ref["tender_id"])] = row
        link = row.get("с_Ссылка") if isinstance(row.get("с_Ссылка"), dict) else {}
        uid = str(link.get("Ид") or "").strip()
        if uid:
            cache[uid] = row
    setattr(rt, _RT_ROWS, cache)
    return refs


def get_cached_row(
    rt: Any, tender_id: str | None, proc_id: str | None
) -> dict[str, Any] | None:
    cache: dict[str, dict[str, Any]] = getattr(rt, _RT_ROWS, {}) or {}
    if tender_id and tender_id in cache:
        return cache[tender_id]
    if proc_id and proc_id in cache:
        return cache[proc_id]
    return None


async def atomform_post(
    page: Any,
    *,
    token: str,
    idmessage: str,
    parameters: dict[str, Any],
    session_tab_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = await page.evaluate(
        """async ({token, idmessage, parameters, sessionTabId, context}) => {
          const body = { command: idmessage, parameters, mobile: false };
          if (context) body.context = context;
          const r = await fetch('/af/atomformapi', {
            method: 'POST',
            headers: {
              'content-type': 'application/json',
              'accept': 'application/json, text/plain, */*',
              'target-system': 'zak',
              'atom.form': '1.20.9',
              'authorization': 'Bearer ' + token,
            },
            body: JSON.stringify({
              header: {
                scale: 100,
                idmessage,
                correlationId: crypto.randomUUID(),
                token,
                atomFormVersion: '1.20.9',
                user_agent: navigator.userAgent,
                lang: 'ru',
                sessionTabId: sessionTabId || crypto.randomUUID(),
              },
              body,
            }),
          });
          const text = await r.text();
          return { status: r.status, text };
        }""",
        {
            "token": token,
            "idmessage": idmessage,
            "parameters": parameters,
            "sessionTabId": session_tab_id,
            "context": context,
        },
    )
    status = int((raw or {}).get("status") or 0)
    text = str((raw or {}).get("text") or "")
    try:
        payload = json.loads(text) if text.startswith("{") else {}
    except Exception:
        payload = {}
    return {"status": status, "text": text, "payload": payload}


async def fetch_procurements(
    rt: Any,
    keywords: str,
    *,
    timeout_ms: int = 90_000,
) -> dict[str, Any]:
    from ...core.browser.primitives import navigate

    page = rt.page
    q = {"link": "procurements", "search": keywords or ""}
    url = f"{HOST}/?{urlencode(q)}"

    payload: dict[str, Any] = {}
    token: str | None = None
    session: str | None = None

    def _on_req(req: Any) -> None:
        if ATOMFORM_PATH not in (req.url or "") and "/af/sse" not in (req.url or ""):
            return
        headers = req.headers or {}
        jwt = headers.get("accesstoken") or headers.get("accessToken")
        if jwt and str(jwt).startswith("eyJ"):
            setattr(rt, _RT_JWT, str(jwt))

    page.on("request", _on_req)
    try:
        try:
            async with page.expect_response(
                lambda r: ATOMFORM_PATH in (r.url or "") and r.status == 200,
                timeout=timeout_ms,
            ) as ri:
                nav = await navigate(rt, url)
            resp = await ri.value
            post = resp.request.post_data or ""
            text = await resp.text()
            try:
                req = json.loads(post) if post.startswith("{") else {}
                token = (req.get("header") or {}).get("token")
                session = (req.get("header") or {}).get("sessionTabId")
            except Exception:
                pass
            try:
                payload = json.loads(text)
            except Exception as e:
                return {
                    "ok": False,
                    "url": page.url,
                    "note": f"getlink json parse failed: {e}",
                    "nav": nav,
                }
        except Exception as e:
            token = getattr(rt, _RT_TOKEN, None)
            session = getattr(rt, _RT_SESSION, None)
            if not token:
                return {
                    "ok": False,
                    "url": getattr(page, "url", ""),
                    "note": f"atomform getlink not captured: {e}",
                }
            posted = await atomform_post(
                page,
                token=token,
                idmessage="GetLink",
                parameters={"Ид": "procurements", "search": keywords or ""},
                session_tab_id=session,
            )
            if posted["status"] != 200 or not posted["payload"]:
                return {
                    "ok": False,
                    "url": page.url,
                    "note": f"atomform post failed status={posted['status']}",
                }
            payload = posted["payload"]
    finally:
        try:
            page.remove_listener("request", _on_req)
        except Exception:
            pass

    _store_token(rt, token, session)
    rows = rows_from_getlink(payload)
    refs = _store_rows(rt, rows)
    summary = list_summary(payload)
    return {
        "ok": bool(refs),
        "url": page.url or url,
        "keywords": keywords,
        "cards": refs,
        "rows": rows,
        "summary": summary,
        "source": "atomformapi:GetLink",
        "note": f"atomform list rows={len(refs)} total={summary.get('total_rows')}",
    }


async def open_procurement_row(rt: Any, row: dict[str, Any]) -> dict[str, Any]:
    token = getattr(rt, _RT_TOKEN, None)
    session = getattr(rt, _RT_SESSION, None)
    if not token:
        boot = await fetch_procurements(rt, "")
        token = getattr(rt, _RT_TOKEN, None)
        session = getattr(rt, _RT_SESSION, None)
        if not token:
            return {"ok": False, "note": boot.get("note") or "no atomform token"}

    posted = await atomform_post(
        rt.page,
        token=token,
        idmessage="GetAction",
        parameters={
            "Действие": "ОткрытьСсылкуИзСписка",
            "Объект": {
                "Вид": "ГА_РаботаПорталаКомандыСайта",
                "Тип": "ОбщийМодуль",
            },
            "ПараметрыДействия": {
                "АктивнаяСтрока": row.get("с_Ид", 0),
                "ИдентификаторФормы": LIST_FORM_ID,
                "ТекущаяСтрока": row,
            },
        },
        session_tab_id=session,
    )
    if posted["status"] != 200 or not posted["payload"]:
        return {
            "ok": False,
            "note": (
                f"open card failed status={posted['status']} "
                f"head={(posted.get('text') or '')[:200]}"
            ),
        }
    payload = posted["payload"]
    setattr(rt, _RT_CARD, payload)
    overview = overview_from_card(payload)
    docs = docs_from_card(payload)
    number = overview.get("fields", {}).get("number") or present(row.get("Номер"))
    link = row.get("с_Ссылка") if isinstance(row.get("с_Ссылка"), dict) else {}
    uuid = str(link.get("Ид") or "").strip() or None
    return {
        "ok": True,
        "source": "atomformapi:GetAction/ОткрытьСсылкуИзСписка",
        "url": card_url(number, uuid),
        "tender_id": number or uuid,
        "overview": overview,
        "documents": docs,
        "note": (
            f"card opened files={len(docs)} "
            f"title={overview.get('window_title') or ''}"
        ),
    }


async def download_storage_file(
    rt: Any,
    *,
    storage_path: str,
    filename: str,
    dest_dir: Path,
) -> dict[str, Any]:
    token = _auth_token(rt)
    if not token:
        return {"ok": False, "note": "no atomform token/jwt"}
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = f"{HOST}/af/storage/?filename={quote(storage_path, safe='')}"
    try:
        resp = await rt.page.request.get(
            url,
            headers={
                "target-system": "zak",
                "atom.form": "1.20.9",
                "authorization": f"Bearer {token}",
                "accept": "*/*",
            },
            timeout=180_000,
        )
        body = await resp.body()
    except Exception as e:
        return {"ok": False, "note": f"storage get failed: {e}", "url": url}
    if resp.status != 200 or len(body) < 64:
        return {
            "ok": False,
            "note": f"storage HTTP {resp.status} bytes={len(body)}",
            "url": url,
        }
    name = _safe_filename(filename or Path(storage_path).name or "rosatom.zip")
    dest = dest_dir / name
    if dest.exists():
        stem, suf = dest.stem, dest.suffix
        n = 2
        while dest.exists():
            dest = dest_dir / f"{stem}_{n}{suf}"
            n += 1
    dest.write_bytes(body)
    sha = hashlib.sha256(body).hexdigest()
    try:
        rt.downloaded_files.append(str(dest))
    except Exception:
        pass
    return {
        "ok": True,
        "file": str(dest),
        "name": dest.name,
        "bytes": len(body),
        "sha256": sha,
        "content_type": (resp.headers or {}).get("content-type") or "",
        "source_url": url,
        "storage_path": storage_path,
    }


async def download_procurement_archive(
    rt: Any,
    *,
    dest_dir: Path,
) -> dict[str, Any]:
    """Pack documents via GetDataFile(ДействиеВыгрузитьФайлы) → MinIO /af/storage."""
    card = getattr(rt, _RT_CARD, None)
    if not isinstance(card, dict):
        return {
            "ok": False,
            "note": "нет кэша карточки — сначала rosatom_open_procurement",
        }
    context = card_form_context(card)
    if not context:
        return {"ok": False, "note": "не удалось собрать context карточки"}
    token = _auth_token(rt)
    session = getattr(rt, _RT_SESSION, None)
    if not token:
        boot = await fetch_procurements(rt, "")
        token = _auth_token(rt)
        session = getattr(rt, _RT_SESSION, None)
        if not token:
            return {"ok": False, "note": boot.get("note") or "no atomform token"}

    posted = await atomform_post(
        rt.page,
        token=token,
        idmessage="GetDataFile",
        parameters={
            "Действие": "ДействиеВыгрузитьФайлы",
            "Объект": {"Вид": "ГА_РаботаСФайлами", "Тип": "ОбщийМодуль"},
            "ПараметрыДействия": {"ИмяТаблицы": "ТаблицаФайлов"},
        },
        session_tab_id=session,
        context=context,
    )
    if posted["status"] != 200 or not posted["payload"]:
        return {
            "ok": False,
            "note": (
                f"GetDataFile failed status={posted['status']} "
                f"head={(posted.get('text') or '')[:240]}"
            ),
        }
    name, path = minio_from_payload(posted["payload"])
    if not path:
        return {
            "ok": False,
            "note": (
                "GetDataFile без НС_ПутьФайлаМинио: "
                f"head={(posted.get('text') or '')[:240]}"
            ),
            "payload_head": (posted.get("text") or "")[:500],
        }
    saved = await download_storage_file(
        rt,
        storage_path=path,
        filename=name or "rosatom-docs.zip",
        dest_dir=dest_dir,
    )
    if saved.get("ok"):
        saved["source"] = "atomformapi:GetDataFile/ДействиеВыгрузитьФайлы+/af/storage"
        saved["minio_name"] = name
        saved["note"] = f"архив документов сохранён: {saved.get('name')}"
    return saved


# --- Structured tools --------------------------------------------------------


class RosatomSearchInput(BaseModel):
    keywords: str = Field(
        default="",
        description=(
            "Текст поиска (GetLink parameters.search). "
            "Может слабо фильтровать выдачу — смотри returned items."
        ),
    )


class RosatomOpenInput(BaseModel):
    tender_id: str = Field(
        description="Номер закупки из rosatom_search (tender_id / number), напр. 248356"
    )
    proc_id: str = Field(
        default="",
        description="Опционально UUID из rosatom_search.proc_id",
    )


def build_rosatom_tools(ctx: PlatformAgentContext) -> dict[str, StructuredTool]:
    out: dict[str, StructuredTool] = {}

    async def rosatom_search(keywords: str = "") -> str:
        kw = (keywords or ctx.keywords or "").strip()
        api = await fetch_procurements(ctx.rt, kw)
        cards = api.get("cards") or []
        items = []
        for c in cards:
            number, proc = parse_card_url(c["url"])
            row = get_cached_row(ctx.rt, c.get("tender_id"), proc)
            nmck = published = status = organizer = None
            if row:
                nmck = present(row.get("НМЦЛотов"))
                published = present(row.get("ДатаПубликацииГК"))
                status = present(row.get("СтатусЗакупочнойПроцедуры"))
                organizer = present(row.get("ОрганизаторЗакупки"))
            items.append(
                {
                    "tender_id": c.get("tender_id"),
                    "number": number or c.get("tender_id"),
                    "proc_id": proc,
                    "title": c.get("title"),
                    "nmck": nmck,
                    "published": published,
                    "status": status,
                    "organizer": organizer,
                    "card_url": c.get("url"),
                    "law": c.get("law") or "223",
                }
            )
        if api.get("url"):
            from ..context import note_results_url

            note_results_url(ctx, str(api["url"]))
        result = {
            "ok": bool(api.get("ok")),
            "action": "rosatom_search",
            "endpoint": "POST /af/atomformapi  idmessage=GetLink",
            "parameters": {"Ид": "procurements", "search": kw},
            "keywords": kw,
            "summary": api.get("summary"),
            "count": len(items),
            "items": items,
            "note": api.get("note"),
            "how_to_use": (
                "Дальше rosatom_open_procurement(tender_id=items[].tender_id, "
                "proc_id=items[].proc_id). Не используй open_platform_search на Росатоме."
            ),
        }
        trace(ctx, "rosatom_search", {"keywords": kw}, result)
        return to_json(result)

    out["rosatom_search"] = StructuredTool.from_function(
        coroutine=rosatom_search,
        name="rosatom_search",
        description=(
            "Росатом ONLY. Поиск/журнал закупок через AtomForm API "
            "(POST /af/atomformapi GetLink, Ид=procurements). "
            "Возвращает items[]: tender_id, proc_id, title, nmck, card_url. "
            "Не для zakupki.gov.ru."
        ),
        args_schema=RosatomSearchInput,
    )

    async def rosatom_open_procurement(tender_id: str, proc_id: str = "") -> str:
        tid = (tender_id or "").strip()
        pid = (proc_id or "").strip() or None
        row = get_cached_row(ctx.rt, tid, pid)
        if row is None:
            await fetch_procurements(ctx.rt, tid or ctx.keywords or "")
            row = get_cached_row(ctx.rt, tid, pid)
        if row is None:
            result = {
                "ok": False,
                "action": "rosatom_open_procurement",
                "message": (
                    "Строка не найдена в кэше. Сначала rosatom_search, "
                    "затем tender_id/proc_id из items[]."
                ),
            }
            trace(ctx, "rosatom_open_procurement", {"tender_id": tid}, result)
            return to_json(result)

        opened = await open_procurement_row(ctx.rt, row)
        overview = opened.get("overview") or {}
        docs = opened.get("documents") or []
        ctx.platform_notes["rosatom_last_tender_id"] = opened.get("tender_id") or tid
        ctx.platform_notes["rosatom_last_documents"] = docs
        ctx.current_tender_id = str(opened.get("tender_id") or tid)
        ctx.current_tender_url = str(opened.get("url") or "")
        result = {
            "ok": bool(opened.get("ok")),
            "action": "rosatom_open_procurement",
            "endpoint": (
                "POST /af/atomformapi  idmessage=GetAction "
                "Действие=ОткрытьСсылкуИзСписка"
            ),
            "tender_id": opened.get("tender_id") or tid,
            "url": opened.get("url"),
            "overview": overview,
            "documents_preview": docs,
            "documents_count": len(docs),
            "note": opened.get("note"),
            "how_to_use": (
                "Метаданные — overview. Список — rosatom_list_files. "
                "Скачать все вложения zip: rosatom_download_files."
            ),
        }
        trace(
            ctx,
            "rosatom_open_procurement",
            {"tender_id": tid, "proc_id": pid},
            result,
        )
        return to_json(result)

    out["rosatom_open_procurement"] = StructuredTool.from_function(
        coroutine=rosatom_open_procurement,
        name="rosatom_open_procurement",
        description=(
            "Росатом ONLY. Открыть карточку (GetAction / ОткрытьСсылкуИзСписка) "
            "по tender_id из rosatom_search. "
            "Возвращает overview и documents_preview. Не для ЕИС."
        ),
        args_schema=RosatomOpenInput,
    )

    async def rosatom_list_files() -> str:
        payload = getattr(ctx.rt, _RT_CARD, None)
        docs_payload: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            docs_payload = docs_from_card(payload)
        else:
            docs_payload = list(ctx.platform_notes.get("rosatom_last_documents") or [])

        items = []
        for d in docs_payload:
            url = str(d.get("url") or "")
            file_id = ""
            if "rosatomFileId=" in url:
                file_id = url.split("rosatomFileId=", 1)[1].split("&", 1)[0]
            items.append(
                {
                    "name": d.get("name"),
                    "kind": d.get("kind"),
                    "file_id": file_id,
                    "url": url,
                }
            )
        result = {
            "ok": bool(items),
            "action": "rosatom_list_files",
            "endpoint": "кэш GetAction (ТаблицаФайлов / ТаблицаФайловИзвещения)",
            "tender_id": ctx.platform_notes.get("rosatom_last_tender_id")
            or ctx.current_tender_id,
            "count": len(items),
            "items": items,
            "how_to_use": (
                "Нужна открытая карточка (rosatom_open_procurement). "
                "Скачать все вложения одним zip: rosatom_download_files."
            ),
            "message": (
                None
                if items
                else "Пусто: сначала rosatom_open_procurement(tender_id=…)."
            ),
        }
        trace(ctx, "rosatom_list_files", {}, result)
        return to_json(result)

    out["rosatom_list_files"] = StructuredTool.from_function(
        coroutine=rosatom_list_files,
        name="rosatom_list_files",
        description=(
            "Росатом ONLY. Список файлов последней открытой карточки. "
            "Возвращает items[]: name, kind, file_id, url. "
            "Сначала rosatom_open_procurement. "
            "Скачивание — rosatom_download_files (zip всех)."
        ),
    )

    async def rosatom_download_files() -> str:
        tid = str(
            ctx.platform_notes.get("rosatom_last_tender_id")
            or ctx.current_tender_id
            or ""
        ).strip()
        if not tid:
            result = {
                "ok": False,
                "action": "rosatom_download_files",
                "message": "Нет текущей карточки — сначала rosatom_open_procurement.",
            }
            trace(ctx, "rosatom_download_files", {}, result)
            return to_json(result)

        folder = ensure_tender_workspace(
            ctx, tid, ctx.current_tender_url or None
        )
        marker = f"rosatom-archive:{tid}"
        if marker in ctx.downloaded_urls:
            result = {
                "ok": True,
                "skipped": True,
                "action": "rosatom_download_files",
                "tender_id": tid,
                "message": "архив этой закупки уже скачан в этом прогоне",
            }
            trace(ctx, "rosatom_download_files", {"tender_id": tid}, result)
            return to_json(result)

        saved = await download_procurement_archive(ctx.rt, dest_dir=folder)
        if saved.get("ok"):
            ctx.downloaded_urls.add(marker)
            src = str(saved.get("source_url") or marker)
            ctx.downloaded_urls.add(src)
            per = ctx.downloads_by_tender.setdefault(tid, set())
            per.add(src)
            try:
                from ...domain import manifest as manifest_mod

                manifest_mod.append_file(
                    Path(folder),
                    name=str(saved.get("name") or "rosatom.zip"),
                    sha256=str(saved.get("sha256") or ""),
                    bytes_count=int(saved.get("bytes") or 0),
                    source_url=src,
                    content_type=str(saved.get("content_type") or "application/zip"),
                    tender_id=tid,
                    platform=ctx.platform,
                    tender_url=str(ctx.current_tender_url or ""),
                )
                saved["manifest"] = str(manifest_mod.manifest_path(Path(folder)))
            except Exception:
                pass

        result = {
            "ok": bool(saved.get("ok")),
            "action": "rosatom_download_files",
            "endpoint": (
                "POST /af/atomformapi GetDataFile ДействиеВыгрузитьФайлы "
                "→ GET /af/storage/?filename=<НС_ПутьФайлаМинио>"
            ),
            "tender_id": tid,
            "file": saved.get("file"),
            "name": saved.get("name"),
            "bytes": saved.get("bytes"),
            "sha256": saved.get("sha256"),
            "minio_name": saved.get("minio_name"),
            "note": saved.get("note") or saved.get("message"),
            "message": saved.get("note") if not saved.get("ok") else None,
            "how_to_use": "Архив всех документов карточки. Дальше mark_processed / finish.",
        }
        if saved.get("manifest"):
            result["manifest"] = saved["manifest"]
        trace(ctx, "rosatom_download_files", {"tender_id": tid}, result)
        return to_json(result)

    out["rosatom_download_files"] = StructuredTool.from_function(
        coroutine=rosatom_download_files,
        name="rosatom_download_files",
        description=(
            "Росатом ONLY. Скачать все документы открытой карточки одним ZIP "
            "(GetDataFile / ДействиеВыгрузитьФайлы → /af/storage MinIO). "
            "Сначала rosatom_open_procurement. Не для ЕИС."
        ),
    )

    return out
