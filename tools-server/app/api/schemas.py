"""API request/response schemas (thin)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .. import config


class PlatformTaskBody(BaseModel):
    platform_url: str = Field(..., description="Tender platform base URL")
    keywords: str = Field(..., description="Search keywords")
    max_new_tenders: int | None = Field(None, ge=1, le=50)
    max_steps: int | None = Field(None, ge=5, le=200)
    download_subdir: str | None = None
    instruction: str | None = None
    tools_mode: str | None = Field(
        None,
        description="platform only (browser removed; use vision_mode=vision).",
    )
    vision_mode: str | None = Field(
        None,
        description="dom | hybrid | vision. Default: AGENT_VISION_MODE.",
    )


class TenderDownloadBody(BaseModel):
    """Single-tender download via adapter or agent (vision_mode)."""

    tender_url: str = Field(..., description="Exact card URL")
    max_files: int = Field(default=5, ge=1, le=30)
    download_subdir: str | None = None
    vision_mode: str | None = Field(
        None,
        description="dom | hybrid | vision. Default: AGENT_VISION_MODE.",
    )
    max_steps: int | None = Field(None, ge=5, le=200)
    instruction: str | None = None


class HealthOut(BaseModel):
    status: str = "ok"
