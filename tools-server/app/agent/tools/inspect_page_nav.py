"""Tool: inspect_page_nav — how to move around current page (pagination / scroll / kind)."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from ...core.browser.page_kind import detect_page_kind
from ...core.browser.url_utils import bump_page_url
from ..context import PlatformAgentContext, note_results_url, to_json, trace

# Back-compat for callers/tests that imported the private name from this module
_bump_page_url = bump_page_url

_NAV_JS = """() => {
  const norm = (v) => String(v ?? '').replace(/\\s+/g, ' ').trim().slice(0, 80);
  const abs = (h) => {
    try { return new URL(String(h || ''), location.href).href; } catch (e) { return ''; }
  };
  const nextRe = /next|следующ|дальше|впер[её]д|>\\s*$|»|›/i;
  const prevRe = /prev|предыдущ|назад|<\\s*$|«|‹/i;
  const pageRe = /^\\s*\\d{1,4}\\s*$/;
  const out = { next: [], prev: [], page_links: [], js_hints: [], search_inputs: 0 };
  const seen = new Set();
  const push = (bucket, item) => {
    const key = (item.href || '') + '|' + (item.text || '') + '|' + (item.kind || '');
    if (seen.has(key)) return;
    seen.add(key);
    bucket.push(item);
  };

  for (const a of document.querySelectorAll('a[href], button, [role="button"], [role="link"]')) {
    const text = norm(a.innerText || a.textContent || a.getAttribute('aria-label') || a.value || '');
    const rawHref = a.getAttribute && a.getAttribute('href');
    const hrefRaw = String(rawHref == null ? '' : rawHref).trim();
    const href = hrefRaw && !hrefRaw.startsWith('javascript:') ? abs(hrefRaw) : '';
    const onclick = String(a.getAttribute && a.getAttribute('onclick') || '');
    if (nextRe.test(text) || nextRe.test(hrefRaw) || /goToPage\\s*\\(\\s*\\d+/i.test(onclick)) {
      if (/goToPage\\s*\\(\\s*(\\d+)/i.test(onclick)) {
        const m = onclick.match(/goToPage\\s*\\(\\s*(\\d+)/i);
        push(out.js_hints, { kind: 'goto_page', page: m ? Number(m[1]) : null, text, onclick: onclick.slice(0, 120) });
      }
      if (href) push(out.next, { text, href });
      else if (text) push(out.next, { text, href: null, note: 'no http href — use click after screenshot or query page bump' });
    } else if (prevRe.test(text) || prevRe.test(hrefRaw)) {
      if (href) push(out.prev, { text, href });
      else if (text) push(out.prev, { text, href: null });
    } else if (pageRe.test(text) && (href || onclick)) {
      push(out.page_links, {
        text,
        href: href || null,
        page: Number(text),
        onclick: onclick ? onclick.slice(0, 80) : null,
      });
    }
  }
  out.search_inputs = [...document.querySelectorAll('input,textarea')].filter(el => {
    const s = ((el.name||'')+(el.id||'')+(el.placeholder||'')+(el.getAttribute('aria-label')||'')).toLowerCase();
    return /search|найти|запрос|query|keyword/.test(s);
  }).length;
  out.next = out.next.slice(0, 5);
  out.prev = out.prev.slice(0, 3);
  out.page_links = out.page_links.slice(0, 12);
  out.js_hints = out.js_hints.slice(0, 8);
  return out;
}"""


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def inspect_page_nav() -> str:
        kind_info = await detect_page_kind(ctx.rt)
        try:
            nav = await ctx.rt.page.evaluate(_NAV_JS)
        except Exception as e:
            nav = {"error": str(e)[:300]}

        url = kind_info.get("url") or ctx.rt.page.url
        if kind_info.get("page_kind") == "search":
            note_results_url(ctx, url)

        suggested = bump_page_url(url)
        next_http = []
        if isinstance(nav, dict):
            next_http = [x.get("href") for x in (nav.get("next") or []) if x.get("href")]

        hints = []
        if kind_info.get("page_kind") == "search":
            hints.append("Это выдача: collect_card_urls → filter_unseen_tenders.")
            if suggested:
                hints.append(f"След. страница (query bump): navigate({suggested!r})")
            if next_http:
                hints.append(f"Или navigate по next href: {next_http[0]!r}")
            elif isinstance(nav, dict) and (nav.get("next") or nav.get("js_hints") or nav.get("page_links")):
                hints.append(
                    "Пагинация без прямого http-href: screenshot → click_xy по «далее»/номеру "
                    "ИЛИ navigate(suggested_next_url)."
                )
            else:
                hints.append(
                    "Явной пагинации в DOM не видно — scroll вниз ОДИН раз + снова inspect_page_nav, "
                    "или suggested_next_url."
                )
        elif kind_info.get("page_kind") == "not_found":
            hints.append("not_found: вернись на results_url / pending_new exact href.")
        elif kind_info.get("page_kind") == "card":
            hints.append("Карточка: save_tender_overview; документы — list_download_links / вкладка на странице.")
        elif kind_info.get("page_kind") == "documents":
            hints.append("Документы: list_download_links → download_url.")

        if suggested:
            ctx.platform_notes["pagination_hint"] = "query page/pageNumber bump works or suggested"
            ctx.platform_notes["suggested_next_url"] = suggested

        result = {
            "ok": True,
            "action": "inspect_page_nav",
            "message": "Навигация страницы распознана",
            **kind_info,
            "pagination": nav if isinstance(nav, dict) else {"raw": nav},
            "suggested_next_url": suggested,
            "hints": hints,
        }
        trace(ctx, "inspect_page_nav", {}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=inspect_page_nav,
        name="inspect_page_nav",
        description=(
            "Понять, КАК устроена навигация на ТЕКУЩЕЙ странице (без написания JS).\n"
            "КОГДА: new_count=0 и нужна следующая страница выдачи; непонятно, search это или card; "
            "после 404/сомнения.\n"
            "ВЕРНЁТ: page_kind, pagination{next,prev,page_links,js_hints}, "
            "suggested_next_url, hints[].\n"
            "Дальше обычно: navigate(suggested_next_url или next.href) — не scroll-loop."
        ),
    )
