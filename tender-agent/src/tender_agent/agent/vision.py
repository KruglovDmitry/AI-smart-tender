"""Зрение: библиотека ai_vision (репозиторий AI-vision) действует в той же вкладке по скриншотам.

Цикл зовёт зрение, когда модель по DOM просит его сама (нужного элемента нет среди
пронумерованных) или действия подряд не меняют страницу. Qwen-VL решает, что делать,
UI-TARS даёт координаты; после шага управление возвращается циклу, который снова
снимает DOM. Данные зрение не извлекает.

Подключение: ``pip install -e ../AI-vision`` и ``TENDER_AGENT_VISION_ENABLED=true``.
Модели и ключи ai_vision берёт из своих переменных ``AV_*``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..browser.runtime import BrowserRuntime
from ..config import Settings
from ..observability.logging import get_logger
from ..observability.trace import current_trace

log = get_logger()

SAFETY = "Не входи в аккаунт, не регистрируйся, не подавай заявки. Если нужен вход или CAPTCHA — ask_user."


@dataclass
class VisionOutcome:
    ok: bool
    note: str
    seconds: float = 0.0
    trace_dir: str = ""


def vision_goal(context: str, need: str) -> str:
    """Только то, что нужно сделать на экране. Общую цель (сколько записей, extract) не передаём:
    иначе зрение пытается выполнить её само и тратит все шаги (ЕИС: сортировку включило, но не сказало done)."""
    return (
        f"{need.rstrip('. ')}. Как только это видно на экране — сразу done. "
        "Данные со страницы не собирай и не извлекай: это сделают после тебя.\n"
        f"Контекст: {context}.\n{SAFETY}"
    )


def vision_available() -> bool:
    try:
        import ai_vision  # noqa: F401
    except ImportError:
        return False
    return True


def configure_vision(av_settings, settings: Settings):
    """Модели зрения из настроек агента (дашборд) поверх AV_* окружения ai_vision."""
    av_settings.headless = settings.headless
    for endpoint, prefix in ((av_settings.planner, "vision_planner"), (av_settings.tars, "vision_tars")):
        for field in ("base_url", "api_key", "model"):
            value = getattr(settings, f"{prefix}_{field}")
            if value:
                setattr(endpoint, field, value.rstrip("/") if field == "base_url" else value)
    return av_settings


async def run_vision_step(runtime: BrowserRuntime, settings: Settings, goal: str) -> VisionOutcome:
    try:
        from ai_vision import VisionAgent, load_settings
    except ImportError:
        return VisionOutcome(False, "ai_vision не установлен")
    av_settings = configure_vision(load_settings(), settings)
    trace = current_trace.get()
    if trace is not None:
        av_settings.runs_dir = trace.vision_dir()
    agent = VisionAgent(av_settings, mode="planner", extract=False)
    log.info("vision step: %s", goal.splitlines()[0][:200])
    try:
        result = await agent.run_on_page(runtime.current_page, goal, max_steps=settings.vision_max_steps)
    except Exception as exc:  # сеть, модели, браузер — цикл продолжит сам
        return VisionOutcome(False, f"зрение упало: {type(exc).__name__}: {exc}")
    if result.final_page is not None and result.final_page is not runtime.page:
        runtime.page = result.final_page
    ok = result.status == "completed"
    note = f"зрение: {result.status}, шагов {len(result.steps)}" + (f" ({result.answer})" if result.answer else "")
    return VisionOutcome(ok, note, result.seconds, result.trace_dir)
