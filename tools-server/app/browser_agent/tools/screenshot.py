"""Tool: screenshot — capture only; vision does the multimodal agent itself."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def screenshot() -> str:
        """Capture viewport; image is attached to the next multimodal model turn."""
        result = await browser_tools.screenshot(ctx.rt)
        # Не отдаём base64 в текст tool result (дорого/бессмысленно) — loop подхватит rt.last_screenshot_b64
        slim = {k: v for k, v in result.items() if k != "image_b64"}
        trace(ctx, "screenshot", {}, slim)
        return to_json(slim)

    return StructuredTool.from_function(
        coroutine=screenshot,
        name="screenshot",
        description=(
            "Снимок viewport. Картинка приходит ТЕБЕ (multimodal) в следующем сообщении — "
            "отдельного VL-вызова нет: ты сам анализируешь экран.\n"
            "КОГДА: верификация после navigate/поиска/карточки/документов; найти поле/кнопку "
            "(координаты x,y); капча/логин/404.\n"
            "АЛЬТЕРНАТИВА: get_page_text — дешёвая проверка текста; "
            "inspect_page_nav / collect_card_urls — структура страницы без картинки.\n"
            "ВЕРНЁТ JSON: ok, url, width, height, has_image. "
            "Координаты для type_text/click_xy — только из свежего кадра (x < width, y < height)."
        ),
    )
