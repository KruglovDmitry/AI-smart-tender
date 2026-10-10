from __future__ import annotations

from pydantic import BaseModel, Field


class Element(BaseModel):
    ref: int
    tag: str = ""
    role: str = ""
    name: str = ""
    href: str | None = None
    placeholder: str | None = None
    label: str = ""
    input_type: str | None = None
    value: str | None = None
    rel: str | None = None
    download: bool = False


class RawCard(BaseModel):
    id: str | None = None
    title: str | None = None
    href: str | None = None
    customer: str | None = None
    published_at: str | None = None
    deadline: str | None = None
    price_text: str | None = None
    amount: str | None = None
    currency: str | None = None
    status: str | None = None
    sources: dict[str, str] = Field(default_factory=dict)


class Observation(BaseModel):
    url: str
    title: str = ""
    page_kind: str = "unknown"
    headings: list[str] = Field(default_factory=list)
    text_excerpt: str = ""
    elements: list[Element] = Field(default_factory=list)
    cards: list[RawCard] = Field(default_factory=list)
    search_ref: int | None = None
    search_value: str = ""
    submit_ref: int | None = None
    sort_ref: int | None = None
    next_ref: int | None = None
    file_refs: list[int] = Field(default_factory=list)
    signature: str = ""
