from pathlib import Path

from tender_agent.sites import BUILTIN_DIR, load, notes_for, parse

NOTE = """---
domain: example.ru
also: lk.example.ru, www.example-old.ru
updated: 2026-10-23
---
## Общее
- Общая заметка.

## Выдача
- Сортировка — список «По умолчанию»,
  пункт «по дате».

## Карточка
- Документы на вкладке «Документы».

## Ловушки
- (2026-10-23) Ловушка.
"""


def test_parse_sections_and_phases():
    notes = parse(NOTE)
    assert notes.domains == ("example.ru", "lk.example.ru", "example-old.ru")
    assert notes.sections["Выдача"] == ["Сортировка — список «По умолчанию», пункт «по дате»."]
    listing, card = notes.text("list"), notes.text("card")
    assert "Сортировка" in listing and "Документы" not in listing and "Ловушка" in listing
    assert "Документы" in card and "Сортировка" not in card and "Общая заметка" in card


def test_notes_by_page_host_and_user_override(tmp_path: Path):
    assert "Пункты — переключатели" in notes_for("https://www.roseltorg.ru/procedures/search", "list")
    assert "Банка России" in notes_for("https://utp.sberbank-ast.ru/VIP/NBT/PurchaseView/1", "card")
    assert notes_for("https://unknown.example/", "list") == ""
    # Своя заметка с тем же доменом заменяет базовую целиком.
    (tmp_path / "roseltorg.md").write_text("---\ndomain: roseltorg.ru\n---\n## Выдача\n- Своя.\n", encoding="utf-8")
    assert notes_for("https://www.roseltorg.ru/", "list", tmp_path) == "[Выдача] Своя."


def test_builtin_notes_are_valid_and_short():
    files = list(BUILTIN_DIR.glob("*.md"))
    assert len(load()) == len(files) >= 10
    for notes in load():
        for phase in ("list", "card"):
            assert len(notes.text(phase)) < 1_500, notes.source
