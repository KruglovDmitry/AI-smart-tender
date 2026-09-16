"""Tool: eval_js — run JS in the page context (DOM helpers)."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ._common import PlatformAgentContext, to_json, trace

_MAX_EXPR = 4000
_MAX_RESULT = 20000


class EvalJsInput(BaseModel):
    expression: str = Field(
        description=(
            "JS-выражение или () => {...} / async () => {...}, ВОЗВРАЩАЕТ JSON-сериализуемое значение. "
            "Пример сбора ссылок: "
            "() => [...document.querySelectorAll('a[href]')].filter(a=>/notice|regNumber|purchase|tender/i.test(a.href))"
            ".slice(0,25).map(a=>({href:a.href,text:(a.innerText||'').trim().slice(0,120)})); "
            "Пример заполнения поиска (подставь keywords в строковый литерал): "
            "() => { const i=[...document.querySelectorAll('input,textarea')]"
            ".find(el=>/search|найти|запрос/i.test((el.name||'')+(el.id||'')+(el.placeholder||''))); "
            "if(!i) return {ok:false,reason:'no_search'}; i.focus(); i.value='KEYWORDS'; "
            "i.dispatchEvent(new Event('input',{bubbles:true})); return {ok:true,id:i.id||i.name}; }"
        )
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def eval_js(expression: str) -> str:
        expr = (expression or "").strip()
        if not expr:
            result = {"ok": False, "action": "eval_js", "message": "expression is empty"}
            trace(ctx, "eval_js", {"expression": ""}, result)
            return to_json(result)
        if len(expr) > _MAX_EXPR:
            result = {
                "ok": False,
                "action": "eval_js",
                "message": f"expression too long (>{_MAX_EXPR})",
            }
            trace(ctx, "eval_js", {"expression_len": len(expr)}, result)
            return to_json(result)
        try:
            value: Any = await ctx.rt.page.evaluate(expr)
            raw = json.dumps(value, ensure_ascii=False, default=str)
            truncated = len(raw) > _MAX_RESULT
            if truncated:
                value = raw[:_MAX_RESULT] + "...(truncated)"
            result = {
                "ok": True,
                "action": "eval_js",
                "message": "evaluated",
                "url": ctx.rt.page.url,
                "value": value,
                "truncated": truncated,
            }
        except Exception as e:
            result = {
                "ok": False,
                "action": "eval_js",
                "message": str(e)[:800],
                "url": ctx.rt.page.url,
            }
        trace(ctx, "eval_js", {"expression": expr[:500]}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=eval_js,
        name="eval_js",
        description=(
            "Выполнить JavaScript в контексте страницы и вернуть значение.\n"
            "КОГДА: собрать список ссылок на карточки из выдачи (href+text по порядку); "
            "заполнить поиск по селектору, если type_text/координаты не работают; "
            "прочитать атрибуты/наличие вкладки документов.\n"
            "АЛЬТЕРНАТИВА: get_page_text — простой текст; screenshot — визуал и координаты; "
            "navigate — уже известный URL.\n"
            "НЕ для: скачивания файлов, finish, выдуманных URL (value — единственный источник).\n"
            "ВЕРНЁТ JSON: ok, url, value (результат JS), truncated; при ошибке ok=false, message. "
            "В summary/finish копируй РЕАЛЬНЫЕ поля из value, не плейсхолдеры."
        ),
        args_schema=EvalJsInput,
    )
