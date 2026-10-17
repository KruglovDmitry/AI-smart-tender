"""Цикл на локальных HTML-фикстурах с моделью-сценарием вместо настоящей LLM.

Проверяется механика цикла: снимок → решение → действие → проверка → история,
а не качество модели.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from tender_agent.agent.loop import page_for_model
from tender_agent.agent.orchestrator import execute
from tender_agent.browser.dom_tools import click_element
from tender_agent.browser.snapshot import get_page_state
from tender_agent.api.schemas import TaskResult
from tender_agent.browser.runtime import BrowserRuntime
from tender_agent.config import Settings
from tender_agent.storage.repository import Repository
from tests.integration.fake_site import fulfill


def _ref(observation: dict, pattern: str) -> int | None:
    for line in observation["elements"]:
        if re.search(pattern, line):
            return int(line[1:line.index("]")])
    return None


class ScriptedModel:
    """Ведёт себя как простая модель: ищет, читает выдачу, листает, завершает."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.extracted: set[str] = set()

    def list_requests(self) -> list[dict]:
        return [r for r in self.requests if not r["task"]["goal"].startswith("Открыта карточка")]

    async def next_action(self, task: dict, observation: dict) -> dict:
        self.requests.append({"task": task, "observation": observation})
        history = " ".join(task["history"])
        if task["goal"].startswith("Открыта карточка"):
            return {"tool": "done", "text": "Прочитано"} if "extract" in history else {"tool": "extract"}
        text = observation["text"]
        if "Вход" in observation["title"]:
            return {"tool": "ask_user", "text": "Площадка просит войти"}
        if "Ничего не найдено" in text and "fill" in history:
            return {"tool": "done", "text": "Ничего не найдено", "empty": True}
        field = _ref(observation, r"placeholder=«Ключевое слово»")
        if field is not None and "fill" not in history:
            return {"tool": "fill", "ref": field, "text": "сервер", "submit": True}
        progress = task["progress"]
        has_cards = _ref(observation, r"→ /tenders/\d+") is not None
        page_key = f"{observation['url']}|{len(text)}"
        if has_cards and page_key not in self.extracted:
            self.extracted.add(page_key)
            return {"tool": "extract"}
        read = int(re.search(r"Прочитано записей: (\d+)", progress).group(1))
        wanted = int(re.search(r"из (\d+)", progress).group(1))
        more = _ref(observation, r"«(Следующая|Показать ещё)»")
        if read < wanted and more is not None and history.count("не изменилась") < 2:
            return {"tool": "click", "ref": more}
        section = _ref(observation, r"«(Закупки|Продолжить)»")
        if section is not None and not has_cards and "click" not in history:
            return {"tool": "click", "ref": section}
        return {"tool": "done", "text": "Готово"}

    async def read_results(self, task: dict, observation: dict) -> dict:
        records = []
        for link in observation["links"]:
            if re.search(r"/tenders/\d+$", link["href"]):
                records.append({"link_ref": link["ref"], "title": link["text"], "number": link["href"].rsplit("/", 1)[-1]})
        return {"records": records}

    async def read_card(self, task: dict, observation: dict) -> dict:
        pdf = _ref(observation, r"spec\.pdf")
        return {"customer": "Заказчик", "published": "2026-10-08", "price": "12 000 000 ₸",
                "okpd2": "26.20.14.000", "documents": [{"ref": pdf, "name": "spec.pdf"}] if pdf else []}


async def _run(runtime: BrowserRuntime, root: Path, text: str, llm: object | None,
               download: bool | None = None, **settings_kwargs: object) -> TaskResult:
    settings = Settings(data_dir=root, headless=True, timeout_seconds=60, max_actions=15, max_pages=4,
                        **settings_kwargs)
    return await execute(text, settings=settings, runtime=runtime, repo=Repository(root / "agent.sqlite3"), llm=llm,
                         download=download)


async def _scenarios(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, headless=True)
    async with BrowserRuntime(settings, tmp_path / "downloads") as runtime:
        assert runtime.context is not None
        await runtime.context.route("**/*", fulfill)
        task = 'Открой https://tenders.example.test/ и верни 3 закупки по ключевому слову "сервер"'

        model = ScriptedModel()
        found = await _run(runtime, tmp_path, task, model)
        # Главная → «Закупки» → поиск → выдача (2 записи) → «Следующая» → ещё 1.
        assert found.status == "completed", found.model_dump_json()
        assert {t.url.rsplit("/", 1)[-1] for t in found.results} == {"101", "102", "308"}  # номеров в тексте нет — только ссылки
        assert found.stats["pages_read"] == 2
        # Карточки: тот же цикл, поля и документы цитатами со страницы; без «скачай» файлы не качаются.
        assert found.stats["card_outcomes"] == {"done": 3}
        assert all(t.published_at == "2026-10-08" and t.price == 12_000_000 for t in found.results)
        assert all(t.documents == [{"name": "spec.pdf", "url": "https://tenders.example.test/files/spec.pdf"}]
                   for t in found.results)
        assert "okpd2" not in found.results[0].details  # такой цитаты на странице нет
        assert found.downloads == []
        last_history = model.list_requests()[-1]["task"]["history"]
        assert any("fill [" in line and "открыта страница" in line for line in last_history)
        assert any(line.split(". ", 1)[1].startswith("extract → записей на странице 2, принято 2") for line in last_history)
        first_page = model.requests[0]["observation"]
        assert first_page["url"] == "https://tenders.example.test/" and "elements" in first_page

        spa = await _run(runtime, tmp_path, 'Открой https://tenders.example.test/spa и верни 2 закупки по ключевому слову "сервер"', ScriptedModel())
        assert {t.url.rsplit("/", 1)[-1] for t in spa.results} == {"201", "308"}, spa.model_dump_json()  # «Показать ещё» дорисовал запись

        noop_model = ScriptedModel()
        noop = await _run(runtime, tmp_path, 'Открой https://tenders.example.test/noop и верни 5 закупок по ключевому слову "сервер"', noop_model)
        assert noop.status == "partial" and [t.url.rsplit("/", 1)[-1] for t in noop.results] == ["101"]
        assert sum("страница не изменилась" in line for line in noop_model.list_requests()[-1]["task"]["history"]) == 2

        # Скачивание: «скачай» в задании; флаг запроса и общий выключатель сильнее текста.
        fetch = 'Открой https://tenders.example.test/tenders?q=сервер и скачай документы 1 закупки по ключевому слову "сервер"'
        saved = await _run(runtime, tmp_path, fetch, ScriptedModel())
        assert [d.status for d in saved.downloads] == ["saved"], saved.model_dump_json()
        assert Path(saved.downloads[0].path).read_bytes().startswith(b"%PDF-")
        assert (await _run(runtime, tmp_path, fetch, ScriptedModel(), download=False)).downloads == []
        assert (await _run(runtime, tmp_path, fetch, ScriptedModel(), downloads_enabled=False)).downloads == []
        plain_task = 'Открой https://tenders.example.test/tenders?q=сервер и верни 1 закупку по ключевому слову "сервер"'
        assert len((await _run(runtime, tmp_path, plain_task, ScriptedModel(), download=True)).downloads) == 1

        empty = await _run(runtime, tmp_path, 'Открой https://tenders.example.test/empty и верни 5 закупок по ключевому слову "сервер"', ScriptedModel())
        assert empty.status == "partial" and empty.results == []
        assert any("не вывод" in item for item in empty.errors)

        login = await _run(runtime, tmp_path, 'Открой https://tenders.example.test/login и верни 5 закупок по ключевому слову "сервер"', ScriptedModel())
        assert login.status == "needs_user"

        captcha_model = ScriptedModel()
        captcha = await _run(runtime, tmp_path, 'Открой https://tenders.example.test/captcha и верни 5 закупок по ключевому слову "сервер"', captcha_model)
        assert captcha.status == "needs_user" and captcha_model.requests == []  # CAPTCHA останавливает до модели

        trap_model = ScriptedModel()
        trap = await _run(runtime, tmp_path, 'Открой https://tenders.example.test/trap и верни 5 закупок по ключевому слову "сервер"', trap_model)
        assert trap.status == "failed" and trap.results == []
        assert "127.0.0.1" not in runtime.current_page.url

        no_model = await _run(runtime, tmp_path, task, None)
        assert no_model.status == "failed" and "Модель не настроена" in no_model.errors[0]

        # Самодельный выпадающий список (div с курсором-«рукой») тоже получает номер и кликается.
        await runtime.current_page.goto("https://tenders.example.test/custom")
        lines = page_for_model(await get_page_state(runtime))["elements"]
        assert "clickable «Сортировать по: Дате обновления»" in " ".join(lines)
        assert not any("Поставка сервера" in line and "clickable" in line for line in lines)  # обёртка над ссылкой
        assert any("«Извещение.pdf»" in line and "(скрыт)" in line for line in lines)  # файл в свёрнутом блоке
        assert not any("О компании" in line for line in lines)  # прочие скрытые ссылки не нужны
        sort_ref = _ref({"elements": lines}, r"Сортировать по")
        await click_element(runtime, sort_ref)
        lines = page_for_model(await get_page_state(runtime))["elements"]
        options = [line for line in lines if "clickable «Дата размещения»" in line]
        assert len(options) == 2 and "item-reverse" in options[1] and "item-reverse" not in options[0]
        assert not any("«Дата размещения Дата размещения»" in line for line in lines)  # сам список — не пункт


def test_loop_on_fixture_site(tmp_path: Path) -> None:
    asyncio.run(_scenarios(tmp_path))
