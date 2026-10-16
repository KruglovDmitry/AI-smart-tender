from tender_agent.agent.vision import vision_goal


def test_goal_carries_only_the_need_context_and_safety() -> None:
    goal = vision_goal("площадка закупок https://zakupki.gov.ru/, запрос «сервер»", "Выбери сортировку по дате размещения")
    assert goal.startswith("Выбери сортировку по дате размещения. Как только это видно на экране — сразу done.")
    assert "Данные со страницы не собирай" in goal
    assert "«сервер»" in goal
    assert "Не входи в аккаунт" in goal
    assert "extract" not in goal
