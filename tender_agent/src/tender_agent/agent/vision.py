"""Запасной шаг по скриншотам: библиотека ai_vision (репозиторий AI-vision).

Вызывается, когда DOM-агент застрял: незнакомая страница без ответа модели или
одно и то же действие не меняет страницу. Qwen-VL решает, что делать, UI-TARS
даёт координаты; действуют они в той же вкладке Playwright, после чего
управление возвращается обычному циклу. Данные vision-шаг не извлекает —
это по-прежнему делает DOM-агент по тексту страницы.

Подключение: ``pip install -e ../AI-vision`` и ``TENDER_AGENT_VISION_ENABLED=true``.
Модели и ключи ai_vision берёт из своих переменных ``AV_*``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..api.schemas import TaskSpec
from ..browser.runtime import BrowserRuntime
from ..config import Settings
from ..observability.logging import get_logger
from ..observability.trace import current_trace

log = get_logger()


@dataclass
class VisionOutcome:
    ok: bool
    note: str
    seconds: float = 0.0
    trace_dir: str = ""


def vision_available() -> bool:
    try:
        import ai_vision  # noqa: F401
    except ImportError:
        return False
    return True


def vision_goal(task: TaskSpec, reason: str, keyword_applied: bool) -> str:
    keyword = task.keywords[0] if task.keywords else task.topic
    want = (
        "Дойди до списка закупок, который уже отфильтрован по этому запросу"
        if keyword_applied
        else f"Найди на площадке поиск закупок, введи запрос «{keyword}» и запусти поиск"
    )
    sort = " Если есть сортировка по дате публикации или размещения, включи её (сначала новые)." if task.sort_by else ""
    return (
        f"{want}.{sort} Когда на экране список закупок по запросу — сразу done.\n"
        f"Почему понадобилась помощь: {reason}.\n"
        "Не входи в аккаунт, не регистрируйся, не подавай заявки. Если нужен вход или CAPTCHA — ask_user."
    )


def documents_goal(tender_title: str | None) -> str:
    subject = f"«{tender_title[:120]}»" if tender_title else "этой закупки"
    return (
        f"Открыта карточка закупки {subject}. Найди и открой раздел или вкладку с документами закупки "
        "(извещение, документация, проект контракта, ТЗ). Когда на экране список файлов документации — сразу done. "
        "Файлы не скачивай. Не входи в аккаунт. Если нужен вход или CAPTCHA — ask_user."
    )


async def run_vision_step(runtime: BrowserRuntime, settings: Settings, task: TaskSpec, reason: str,
                          keyword_applied: bool, goal: str | None = None) -> VisionOutcome:
    try:
        from ai_vision import VisionAgent, load_settings
    except ImportError:
        return VisionOutcome(False, "ai_vision не установлен")
    av_settings = load_settings()
    av_settings.headless = settings.headless
    trace = current_trace.get()
    if trace is not None:
        av_settings.runs_dir = trace.vision_dir()
    agent = VisionAgent(av_settings, mode="planner", extract=False)
    goal = goal or vision_goal(task, reason, keyword_applied)
    log.info("vision step: %s", reason)
    try:
        result = await agent.run_on_page(runtime.current_page, goal, max_steps=settings.vision_max_steps)
    except Exception as exc:  # сеть, модели, браузер — DOM-агент продолжит сам
        return VisionOutcome(False, f"vision-шаг упал: {type(exc).__name__}: {exc}")
    if result.final_page is not None and result.final_page is not runtime.page:
        runtime.page = result.final_page
    ok = result.status == "completed"
    note = f"vision-шаг: {result.status}, шагов {len(result.steps)}" + (f" ({result.answer})" if result.answer else "")
    return VisionOutcome(ok, note, result.seconds, result.trace_dir)
