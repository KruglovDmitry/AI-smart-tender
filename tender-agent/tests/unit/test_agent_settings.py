from pathlib import Path

from fastapi.testclient import TestClient

from tender_agent.agent.vision import configure_vision
from tender_agent.agent_settings import apply_overrides, describe, effective, load_overrides, save_overrides
from tender_agent.config import Settings


def test_dashboard_values_override_env_and_keys_are_masked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AV_TARS_BASE_URL", "http://10.0.0.1:8000/v1")
    monkeypatch.delenv("TENDER_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    save_overrides(tmp_path, {"llm_api_key": "sk-secret-1234", "headless": "false", "vision_max_calls": "3"})
    rows = {row["key"]: row for row in describe(tmp_path)}
    assert rows["llm_api_key"] == {**rows["llm_api_key"], "value": "••••1234", "source": "dashboard", "set": True}
    assert rows["headless"]["value"] is False and rows["vision_max_calls"]["value"] == 3
    assert rows["vision_tars_base_url"]["source"] == "env"
    assert rows["llm_model"]["source"] == "default"

    settings = apply_overrides(Settings(data_dir=tmp_path))
    assert (settings.llm_api_key, settings.headless, settings.vision_max_calls) == ("sk-secret-1234", False, 3)

    save_overrides(tmp_path, {"llm_api_key": "", "headless": None})  # сброс → снова .env / умолчание
    assert load_overrides(tmp_path) == {"vision_max_calls": 3}


def test_settings_file_stops_taking_secrets_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LLM_API_KEY", "from-env-secret")
    monkeypatch.setenv("AV_PLANNER_API_KEY", "from-env-vl")
    save_overrides(tmp_path, {"llm_model": "deepseek-flash"})
    rows = {row["key"]: row for row in describe(tmp_path)}
    assert rows["llm_api_key"]["source"] == "default" and rows["llm_api_key"]["set"] is False
    assert effective(tmp_path, "llm_api_key") == ""
    assert effective(tmp_path, "vision_planner_api_key") == ""
    settings = apply_overrides(Settings(
        data_dir=tmp_path, llm_api_key="from-env-secret", vision_planner_api_key="from-env-vl",
    ))
    assert (settings.llm_api_key, settings.vision_planner_api_key, settings.llm_model) == ("", "", "deepseek-flash")


def test_vision_models_from_settings() -> None:
    class Endpoint:
        def __init__(self) -> None:
            self.base_url, self.api_key, self.model = "env-url", "env-key", "env-model"

    class AV:
        headless = True
        planner = Endpoint()
        tars = Endpoint()

    av = configure_vision(AV(), Settings(headless=False, vision_tars_base_url="http://tars/v1/", vision_planner_api_key="k"))
    assert av.headless is False
    assert (av.tars.base_url, av.tars.model) == ("http://tars/v1", "env-model")
    assert (av.planner.api_key, av.planner.base_url) == ("k", "env-url")


def test_dashboard_file_clears_vision_key_from_environment(tmp_path: Path) -> None:
    save_overrides(tmp_path, {"llm_model": "m"})

    class Endpoint:
        def __init__(self) -> None:
            self.base_url, self.api_key, self.model = "env-url", "env-key", "env-model"

    class AV:
        headless = True
        planner = Endpoint()
        tars = Endpoint()

    av = configure_vision(AV(), Settings(data_dir=tmp_path))
    assert av.planner.api_key == "" and av.tars.api_key == ""
    assert av.planner.base_url == "env-url"


def test_ground_fallback_uses_planner_key_and_can_be_switched_off() -> None:
    class Endpoint:
        def __init__(self, model: str) -> None:
            self.base_url, self.api_key, self.model = "env-url", "env-key", model

    class AV:
        headless = True

        def __init__(self) -> None:
            self.planner, self.tars, self.ground_fallback = Endpoint("qwen3-vl-plus"), Endpoint("ui-tars"), Endpoint("qwen3-vl-plus")

    av = configure_vision(AV(), Settings(vision_planner_api_key="k", vision_fallback_model="qwen3-vl-flash"))
    assert (av.ground_fallback.api_key, av.ground_fallback.model) == ("k", "qwen3-vl-flash")
    assert configure_vision(AV(), Settings(vision_fallback_model="-")).ground_fallback is None


def test_check_uses_typed_key_without_saving_a_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TENDER_AGENT_DATA_DIR", str(tmp_path))
    save_overrides(tmp_path, {"llm_model": "deepseek-flash", "llm_base_url": "https://example.invalid/v1"})
    seen: dict = {}

    class Response:
        status_code = 401
        text = "invalid"

    class Client:
        def __init__(self, timeout: float) -> None:
            del timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, json: dict, headers: dict) -> Response:
            seen["url"] = url
            seen["auth"] = headers["Authorization"]
            seen["model"] = json["model"]
            return Response()

    monkeypatch.setattr("tender_agent.server.httpx.AsyncClient", Client)
    from tender_agent.server import app

    client = TestClient(app)
    missing = client.post("/settings/check", json={"target": "dom"}).json()
    assert missing == {"ok": False, "detail": "Ключ не задан. Вставьте его в поле и нажмите «Проверить»."}
    assert "auth" not in seen

    checked = client.post("/settings/check", json={"target": "dom", "values": {"llm_api_key": "sk-typed"}}).json()
    assert checked["ok"] is False and "не сохранено" not in checked["detail"]
    assert seen["auth"] == "Bearer sk-typed" and seen["model"] == "deepseek-flash"
    assert effective(tmp_path, "llm_api_key") == ""


def test_settings_api_is_hidden_from_open_webui(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TENDER_AGENT_DATA_DIR", str(tmp_path))
    from tender_agent.server import app

    client = TestClient(app)
    saved = client.put("/settings", json={"values": {"vision_enabled": True, "llm_api_key": "abcdefgh9999"}}).json()
    rows = {row["key"]: row for row in saved["options"]}
    assert rows["vision_enabled"]["value"] is True and rows["llm_api_key"]["value"] == "••••9999"
    assert "abcdefgh9999" not in client.get("/settings").text
    assert client.put("/settings", json={"values": {"nope": 1}}).status_code == 400
    paths = client.get("/openapi.json").json()["paths"]
    assert "/settings" not in paths and "/search_tenders" in paths
