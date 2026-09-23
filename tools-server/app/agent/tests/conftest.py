"""Pytest fixtures for platform agent integration tests."""

from __future__ import annotations

import pytest

from app import config as app_config


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: live LLM + Playwright + real tender site (opt-in)",
    )


@pytest.fixture
def require_agent_llm() -> None:
    if not app_config.AGENT_LLM_BASE_URL or not app_config.AGENT_LLM_API_KEY:
        pytest.skip("AGENT_LLM_BASE_URL / AGENT_LLM_API_KEY not configured")
