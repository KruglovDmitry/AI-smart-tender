import asyncio
import json

from tender_agent.sites import notes_for
from tender_agent.sites.learning import Candidate, CandidateStore, learn, update

GOOD = {"status": "completed", "stats": {"loop_outcome": "done"}, "seconds": 100}
BAD = {"status": "partial", "stats": {"loop_outcome": "timeout"}, "seconds": 300}


def test_candidate_becomes_active_after_two_good_runs_and_back_after_two_bad():
    items = update([], {"new": [{"section": "Выдача", "text": "Сортировка — список «По умолчанию».", "kind": "add"}]},
                   "run1", GOOD, today="2026-10-26")
    assert [(c.id, c.status, c.confirms) for c in items] == [("c1", "candidate", ["run1"])]
    items = update(items, {"confirm": ["c1"]}, "run2", {**GOOD, "seconds": 120}, today="2026-10-26")
    assert items[0].status == "active" and items[0].since == "2026-10-26"
    items = update(items, {}, "run3", BAD)
    assert items[0].status == "active" and items[0].strikes == 1
    items = update(items, {}, "run4", {**GOOD, "seconds": 500})  # втрое дольше — тоже плохо
    assert items[0].status == "candidate" and items[0].confirms == []


def test_bad_run_does_not_confirm_and_refute_resets_candidate():
    items = [Candidate(id="c1", section="Карточка", text="Документы — вкладка «Файлы».", confirms=["run1"])]
    items = update(items, {"confirm": ["c1"]}, "run2", BAD)
    assert items[0].confirms == ["run1"] and items[0].status == "candidate"
    items = update(items, {"refute": ["c1"]}, "run3", GOOD)
    assert items[0].confirms == [] and items[0].refutes == ["run3"]


def test_new_items_are_checked():
    proposal = {"new": [{"section": "Чепуха", "text": "x"}, {"section": "Выдача", "text": ""},
                        {"section": "Ловушки", "text": "Окно обучения перекрывает клики."},
                        {"section": "Ловушки", "text": "окно обучения перекрывает клики"}]}
    items = update([], proposal, "run1", GOOD)
    assert [c.text for c in items] == ["Окно обучения перекрывает клики."]


def test_restated_candidate_counts_as_confirmation():
    items = [Candidate(id="c1", section="Ловушки", text="Чекбокс перекрыт — фильтр адресом statuses[]=1.",
                       confirms=["run1"])]
    proposal = {"new": [{"section": "Ловушки", "text": "Пункт меню не находится — ставь statuses[]=1 в адресе.",
                         "same_as": "c1"},
                        {"section": "Выдача", "text": "Чужой id не мешает.", "same_as": "c9"}]}
    items = update(items, proposal, "run2", GOOD)
    assert items[0].confirms == ["run1", "run2"] and items[0].status == "active"
    assert [c.text for c in items[1:]] == ["Чужой id не мешает."]


def test_active_items_reach_the_model_and_removed_lines_do_not(tmp_path):
    (tmp_path / "x.ru.md").write_text("---\ndomain: x.ru\n---\n## Выдача\n- Фильтр по этапу работает.\n- Сортировка сверху.\n",
                                      encoding="utf-8")
    store = CandidateStore(tmp_path)
    store.save("x.ru", [
        Candidate(id="c1", section="Выдача", text="Фильтр по этапу работает.", kind="remove", status="active", since="2026-10-26"),
        Candidate(id="c2", section="Выдача", text="«Показать ещё» внизу.", status="active", since="2026-10-26"),
        Candidate(id="c3", section="Выдача", text="Ещё не подтверждено.", status="candidate"),
    ])
    text = notes_for("https://www.x.ru/search", "list", tmp_path)
    assert text == "[Выдача] Сортировка сверху.\n[Выдача] (авто 2026-10-26) «Показать ещё» внизу."
    assert notes_for("https://x.ru/card", "card", tmp_path) == ""


def test_learn_reads_trace_and_saves_candidates(tmp_path):
    runs, sites = tmp_path / "runs", tmp_path / "sites"
    trace = runs / "20261026-120000-x.ru"
    trace.mkdir(parents=True)
    steps = [{"step": 1, "phase": "list", "url": "https://x.ru/s", "decision": {"tool": "click", "thought": "сортировка"},
              "note": "click → страница изменилась"},
             {"step": 2, "phase": "card", "url": "https://other.ru/c", "decision": {"tool": "done"}, "note": ""}]
    (trace / "steps.jsonl").write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in steps), encoding="utf-8")
    (trace / "result.json").write_text(json.dumps({"status": "completed", "stats": {"loop_outcome": "done"}}),
                                       encoding="utf-8")
    seen = {}

    class FakeLLM:
        async def propose_notes(self, task, observation):
            seen.update(task=task, observation=observation)
            return {"new": [{"section": "Выдача", "text": "Сортировка — кнопка над выдачей.", "kind": "add"}]}

    proposal = asyncio.run(learn(FakeLLM(), trace, sites, {"url": "https://www.x.ru/", **GOOD}))
    assert proposal["new"]
    assert [s["n"] for s in seen["observation"]["steps"]] == [1]  # шаг на другом сайте не о нашей площадке
    assert [c.text for c in CandidateStore(sites).load("x.ru")] == ["Сортировка — кнопка над выдачей."]
