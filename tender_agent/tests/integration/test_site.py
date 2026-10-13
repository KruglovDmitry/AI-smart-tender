from __future__ import annotations

import asyncio
from pathlib import Path

from tender_agent.agent.orchestrator import execute
from tender_agent.api.schemas import TaskResult
from tender_agent.browser.observations import take_screenshot
from tender_agent.browser.runtime import BrowserRuntime
from tender_agent.config import Settings
from tender_agent.storage.repository import Repository, SiteProfile
from tests.integration.fake_site import fulfill


class _Continue:
    async def decide(self, task: dict, observation: dict) -> dict:
        for element in observation["elements"]:
            if "Продолжить" in (element.get("name") or ""):
                return {"tool": "click", "element_ref": element["ref"], "expected": "changed"}
        return {"tool": "finish", "element_ref": None, "expected": "changed"}


def _settings(root: Path, **kwargs: object) -> Settings:
    data = {
        "data_dir": root,
        "headless": True,
        "timeout_seconds": 45,
        "max_actions": 25,
        "max_pages": 4,
        "max_retries": 2,
    }
    data.update(kwargs)
    return Settings(**data)


_case = 0


async def _run(
    runtime: BrowserRuntime,
    root: Path,
    text: str,
    *,
    llm: object | None = None,
    prepare=None,
    **settings_kwargs: object,
) -> tuple[TaskResult, Repository]:
    global _case
    _case += 1
    folder = root / f"case-{_case}"
    folder.mkdir(parents=True, exist_ok=True)
    settings = _settings(folder, **settings_kwargs)
    repo = Repository(folder / "agent.sqlite3")
    if prepare:
        prepare(repo)
    result = await execute(text, settings=settings, runtime=runtime, repo=repo, llm=llm)
    return result, repo


def _dump(result: TaskResult) -> str:
    return result.model_dump_json()


async def _scenarios(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    async with BrowserRuntime(settings, tmp_path / "downloads") as runtime:
        assert runtime.context is not None
        await runtime.context.route("**/*", fulfill)
        shot = None
        await runtime.current_page.goto("https://tenders.example.test/", wait_until="domcontentloaded")
        shot = await take_screenshot(runtime)
        assert shot.startswith(b"\x89PNG")

        latest, _repo = await _run(
            runtime,
            tmp_path,
            'Начни мониторинг площадки https://tenders.example.test/ и верни последние 1 закупок на тему "сервер"',
        )
        assert latest.status == "completed", _dump(latest)
        assert len(latest.results) == 1
        winner = latest.results[0]
        assert winner.id == "308"
        assert winner.published_at == "2026-10-08"
        assert winner.price == 12_000_000
        assert winner.currency == "KZT"
        assert winner.customer == "Заказчик"
        assert winner.verification_status == "verified"
        assert latest.stats["site_sort_used"] is True
        assert "308" in (winner.url or "")

        limited, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/tenders?q=сервер и верни последние 1 закупок на тему "сервер"',
            max_pages=1,
        )
        assert limited.status == "partial", _dump(limited)
        assert limited.results and limited.results[0].id != "308"
        assert any("лимит страниц" in item for item in limited.errors)

        downloaded, _repo = await _run(
            runtime,
            tmp_path,
            'Найди закупки по ключевому слову "сервер" на https://tenders.example.test/tenders?q=сервер '
            "и скачай документы из последних 1 карточек",
        )
        assert downloaded.status == "completed", _dump(downloaded)
        assert downloaded.results[0].id == "308"
        assert downloaded.downloads and downloaded.downloads[0].status == "saved"
        saved = Path(downloaded.downloads[0].path)
        assert saved.is_file() and saved.stat().st_size > 0
        assert saved.read_bytes().startswith(b"%PDF-")

        spa, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/spa и верни последние 1 закупок по ключевому слову "сервер"',
        )
        assert spa.status == "completed", _dump(spa)
        assert spa.results[0].id == "308"
        assert spa.stats["site_sort_used"] is False

        mixed, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/mixed и верни 1 закупку по ключевому слову "сервер"',
        )
        assert mixed.status == "completed", _dump(mixed)
        assert [item.id for item in mixed.results] == ["1"]

        ambiguous, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/ambiguous и верни 2 закупки по ключевому слову "сервер"',
        )
        assert ambiguous.status == "completed", _dump(ambiguous)
        assert len({item.url for item in ambiguous.results}) == 2

        plain, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/plain и верни 1 закупку по ключевому слову "сервер"',
        )
        assert plain.status == "completed", _dump(plain)
        assert plain.results[0].published_at is None
        assert (plain.results[0].url or "").endswith("/tenders/7")

        empty, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/empty и верни последние 5 закупок по ключевому слову "сервер"',
        )
        assert empty.status == "partial", _dump(empty)
        assert empty.results == []
        assert any("не вывод" in item for item in empty.errors)

        missing, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/missing и верни последние 5 закупок по ключевому слову "сервер"',
        )
        assert missing.status == "failed", _dump(missing)

        login, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/login и верни последние 5 закупок по ключевому слову "сервер"',
        )
        assert login.status == "needs_user", _dump(login)
        captcha, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/captcha и верни последние 5 закупок по ключевому слову "сервер"',
        )
        assert captcha.status == "needs_user", _dump(captcha)

        repeated, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/noop и верни последние 5 закупок по ключевому слову "сервер"',
        )
        assert repeated.status == "partial", _dump(repeated)
        assert repeated.results and repeated.results[0].id == "101"
        assert any("не изменилась" in item or "повторяется" in item for item in repeated.errors)

        trapped, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/trap и верни последние 5 закупок по ключевому слову "сервер"',
        )
        assert trapped.status == "failed", _dump(trapped)
        assert trapped.results == []

        unknown, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/unknown и верни последние 1 закупок по ключевому слову "сервер"',
        )
        assert unknown.status == "failed", _dump(unknown)
        assert unknown.stats["llm_calls"] == 1, _dump(unknown)

        guided, _repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/unknown и верни последние 1 закупок по ключевому слову "сервер"',
            llm=_Continue(),
        )
        assert guided.status == "completed", _dump(guided)
        assert guided.results[0].id == "308"
        assert guided.stats["llm_calls"] >= 1

        def seed(repo: Repository) -> None:
            repo.save_profile(
                SiteProfile(
                    host="tenders.example.test",
                    search_placeholder="устаревший плейсхолдер",
                    card_selector=".does-not-exist",
                )
            )

        stale, repo = await _run(
            runtime,
            tmp_path,
            'Открой https://tenders.example.test/tenders?q=сервер и верни последние 1 закупок на тему "сервер"',
            prepare=seed,
        )
        assert stale.status == "completed", _dump(stale)
        assert stale.stats["profile_rejected"] is True
        assert stale.results[0].id == "308"
        saved_profile = repo.load_profile("tenders.example.test")
        assert saved_profile is not None
        assert saved_profile.error_count >= 1
        assert "tender-card" in saved_profile.card_selector


def test_local_fixture_site(tmp_path: Path) -> None:
    asyncio.run(_scenarios(tmp_path))
