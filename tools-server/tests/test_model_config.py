"""Ключи моделей: файл дашборда важнее окружения, пустой файл окружение не возвращает."""

from __future__ import annotations

import importlib
import json


def _config(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("AGENT_LLM_API_KEY", "from-env")
    monkeypatch.setenv("AGENT_LLM_MODEL", "deepseek-flash")
    monkeypatch.setenv("AV_PLANNER_API_KEY", "from-env-vl")
    from app import config

    importlib.reload(config)
    from app import model_config

    return importlib.reload(model_config)


def test_without_dashboard_file_env_is_used(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch)
    assert cfg.llm_config() == ("https://api.deepseek.com", "from-env", "deepseek-flash")
    base, key, model = cfg.vl_config()
    assert key == "from-env-vl" and model == "qwen3-vl-plus" and base.startswith("https://")


def test_dashboard_file_hides_env_secrets(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch)
    path = tmp_path / "tenders" / "_agent" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "llm_base_url": "https://api.deepseek.com",
        "llm_model": "deepseek-flash",
        "vision_planner_model": "qwen3-vl-plus",
    }), encoding="utf-8")
    try:
        cfg.llm_config()
    except RuntimeError as exc:
        assert "дашборда" in str(exc)
    else:
        raise AssertionError("empty dashboard key must not fall back to env")
    try:
        cfg.vl_config()
    except RuntimeError as exc:
        assert "дашборда" in str(exc)
    else:
        raise AssertionError("empty vision key must not fall back to env")

    path.write_text(json.dumps({
        "llm_base_url": "https://api.deepseek.com",
        "llm_api_key": "from-dashboard",
        "llm_model": "deepseek-flash",
        "vision_planner_api_key": "vl-dashboard",
    }), encoding="utf-8")
    assert cfg.llm_config()[1] == "from-dashboard"
    assert cfg.vl_config()[1] == "vl-dashboard"
