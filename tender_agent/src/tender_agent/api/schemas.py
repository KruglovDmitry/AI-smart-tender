from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


TaskStatus = Literal["completed", "partial", "needs_user", "failed"]
VerificationStatus = Literal["verified", "needs_review", "incomplete"]


class TaskSpec(BaseModel):
    url: str
    topic: str
    keywords: list[str]
    limit: int = 5
    sort_by: Literal["published_at"] | None = None
    filters: dict[str, str] = Field(default_factory=dict)
    download_documents: bool = False
    max_pages: int = 5
    max_actions: int = 40
    timeout_seconds: int = 120


class TenderResult(BaseModel):
    id: str | None = None
    title: str | None = None
    url: str | None = None
    customer: str | None = None
    published_at: str | None = None
    deadline: str | None = None
    price: float | None = None
    currency: str | None = None
    status: str | None = None
    matched_keyword: str | None = None
    verification_status: VerificationStatus = "incomplete"
    document_urls: list[str] = Field(default_factory=list)
    sources: dict[str, str] = Field(default_factory=dict)


class DownloadRecord(BaseModel):
    source_url: str
    path: str
    name: str
    size: int
    status: Literal["saved", "failed"]
    tender_url: str | None = None
    detail: str = ""


class TaskResult(BaseModel):
    status: TaskStatus
    task: TaskSpec | None = None
    results: list[TenderResult] = Field(default_factory=list)
    downloads: list[DownloadRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    stats: dict[str, int | bool] = Field(default_factory=dict)
