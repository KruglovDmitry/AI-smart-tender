"""Platform agent loop — re-exports current browser_agent runner (Phase 3 entry)."""

from ..browser_agent.agent import run_platform_task

__all__ = ["run_platform_task"]
