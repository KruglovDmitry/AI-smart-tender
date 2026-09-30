"""API request/response schemas (thin)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlatformTaskBody(BaseModel):
    platform_url: str = Field(..., description="Tender platform base URL")
    keywords: str = Field(..., description="Search keywords")
    max_new_tenders: int | None = Field(None, ge=1, le=50)
    max_steps: int | None = Field(None, ge=5, le=200)
    download_subdir: str | None = None
    instruction: str | None = None
    tools_mode: str | None = Field(
        None,
        description="platform only.",
    )
    # Deprecated — ignored (vision layer removed).
    vision_mode: str | None = Field(None, description="Deprecated, ignored.")


class TenderDownloadBody(BaseModel):
    """Single-tender download via adapter or optional agent loop."""

    tender_url: str = Field(..., description="Exact card URL")
    max_files: int = Field(default=5, ge=1, le=30)
    download_subdir: str | None = None
    use_agent: bool = Field(
        default=False,
        description="If true, run platform agent loop instead of direct adapter path.",
    )
    # Deprecated — ignored.
    vision_mode: str | None = Field(None, description="Deprecated, ignored.")
    max_steps: int | None = Field(None, ge=5, le=200)
    instruction: str | None = None


class HealthOut(BaseModel):
    status: str = "ok"
