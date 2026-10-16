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


def vision_goal(goal: str, need: str) -> str:
    return f"{need}. Когда это сделано — сразу done.\nОбщая цель: {goal}\n{SAFETY}"


async def run_vision_step(runtime: BrowserRuntime, settings: Settings, goal: str) -> VisionOutcome:
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
