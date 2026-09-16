"""Tool: navigate."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ...browser_tool import tools as browser_tools
from ._common import PlatformAgentContext, to_json, trace


class NavigateInput(BaseModel):
    url: str = Field(
        description="Полный http(s) URL. Только абсолютные ссылки с хостом; не javascript:/mailto:."
    )


def make_tool(ctx: PlatformAgentContext) -> StructuredTool:
    async def navigate(url: str) -> str:
        result = await browser_tools.navigate(ctx.rt, url)
        trace(ctx, "navigate", {"url": url}, result)
        return to_json(result)

    return StructuredTool.from_function(
        coroutine=navigate,
        name="navigate",
        description=(
            "Открыть URL в браузере (полный переход страницы).\n"
            "КОГДА: старт на platform_url; переход на карточку закупки; раздел документов; "
            "повторный поиск, если известен рабочий URL выдачи с этой же площадки.\n"
            "АЛЬТЕРНАТИВА: click_xy — если нужно нажать кнопку/вкладку без известного URL; "
            "press_key/scroll — если страница уже нужная.\n"
            "НЕ для: скачивания файлов (→ download_url / click_xy expect_download).\n"
            "ВЕРНЁТ JSON: ok, action, message, url, title (текущие после загрузки). "
            "При ошибке ok=false и message с причиной."
        ),
        args_schema=NavigateInput,
    )
