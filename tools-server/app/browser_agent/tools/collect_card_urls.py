"""Tool: collect_card_urls — safe DOM scrape of purchase card links on results page."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._common import PlatformAgentContext, note_results_url, to_json, trace

# Robust: href via getAttribute + String(); skip javascript:/mailto:; keep http(s) only.
_COLLECT_JS = """() => {
  const out = [];
  const seen = new Set();
  const looksCard = (h, t) => {
    const s = (h + ' ' + t).toLowerCase();
    return /notice|purchase|tender|order\\/|regnumber|reg_number|извещ|закупк|лот\\b/.test(s);
  };
  for (const a of document.querySelectorAll('a[href]')) {
    const raw = a.getAttribute('href');
    const href = String(raw == null ? '' : raw).trim();
    if (!href || href.startsWith('javascript:') || href.startsWith('mailto:') || href === '#') continue;
    let abs = href;
    try { abs = new URL(href, location.href).href; } catch (e) { continue; }
    if (!/^https?:/i.test(abs)) continue;
    const text = String(a.innerText || a.textContent || a.getAttribute('title') || '')
      .replace(/\\s+/g, ' ').trim().slice(0, 120);
    if (!looksCard(abs, text)) continue;
    if (seen.has(abs)) continue;
    seen.add(abs);
    out.push({ href: abs, text });
    if (out.length >= 40) break;
  }
  return { url: location.href, count: out.length, links: out };
}"""


class CollectCardUrlsInput(BaseModel):
    limit: int = Field(
        default=40,
        description="Максимум ссылок (порядок сверху вниз как в DOM)",
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def collect_card_urls(limit: int = 40) -> str:
        try:
            value = await ctx.rt.page.evaluate(_COLLECT_JS)
        except Exception as e:
            result = {
                "ok": False,
                "action": "collect_card_urls",
                "message": str(e)[:500],
                "url": ctx.rt.page.url,
            }
            trace(ctx, "collect_card_urls", {"limit": limit}, result)
            return to_json(result)

        links = []
        if isinstance(value, dict):
            for item in (value.get("links") or [])[: max(1, limit)]:
                if isinstance(item, dict) and item.get("href"):
                    links.append(
                        {
                            "href": str(item["href"]),
                            "text": str(item.get("text") or "")[:120],
                        }
                    )
            page_url = str(value.get("url") or ctx.rt.page.url)
        else:
            page_url = ctx.rt.page.url

        note_results_url(ctx, page_url)
        urls = [x["href"] for x in links]
        result = {
            "ok": True,
            "action": "collect_card_urls",
            "message": (
                f"Собрано {len(urls)} href карточек. "
                "Дальше filter_unseen_tenders(urls) и navigate ТОЛЬКО по new[].tender_url "
                "(копируй href как есть — не собирай URL вручную)."
            ),
            "url": page_url,
            "count": len(urls),
            "urls": urls,
            "links": links,
        }
        trace(ctx, "collect_card_urls", {"limit": limit}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=collect_card_urls,
        name="collect_card_urls",
        description=(
            "Безопасно собрать href карточек закупок с ТЕКУЩЕЙ страницы выдачи "
            "(порядок сверху вниз).\n"
            "КОГДА: на странице результатов поиска, перед filter_unseen_tenders.\n"
            "ВЕРНЁТ JSON: urls[], links[{href,text}], url страницы. "
            "Запоминает results_url в сессии."
        ),
        args_schema=CollectCardUrlsInput,
    )
