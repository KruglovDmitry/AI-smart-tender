from tender_agent.agent.vision import vision_goal


def test_goal_carries_need_goal_and_safety() -> None:
    goal = vision_goal("Найди закупки по запросу «сервер»", "Нажми на иконку поиска")
    assert goal.startswith("Нажми на иконку поиска")
    assert "«сервер»" in goal
    assert "Не входи в аккаунт" in goal
