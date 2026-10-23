from pathlib import Path

from fastapi.testclient import TestClient

from tender_agent.agent.vision import configure_vision
from tender_agent.agent_settings import apply_overrides, describe, load_overrides, save_overrides
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
