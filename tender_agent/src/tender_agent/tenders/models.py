from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


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

    @field_validator("tag", "role", "name", "label", mode="before")
    @classmethod
    def _required_text(cls, value: object) -> str:
        return _text(value)

    @field_validator("href", "placeholder", "input_type", "value", "rel", mode="before")
    @classmethod
    def _optional(cls, value: object) -> str | None:
        return _optional_text(value)


class PageField(BaseModel):
    id: int
    label: str = ""
    value: str = ""

    @field_validator("label", "value", mode="before")
    @classmethod
    def _required_text(cls, value: object) -> str:
        return _text(value)


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
    # Текст блока карточки в выдаче: по нему модель дочитывает поля (tenders/refine.py).
    block_text: str | None = None


class Observation(BaseModel):
    url: str
    title: str = ""
    page_kind: str = "unknown"
    headings: list[str] = Field(default_factory=list)
    text_excerpt: str = ""
    content_text: str = ""
    fields: list[PageField] = Field(default_factory=list)
    elements: list[Element] = Field(default_factory=list)
    cards: list[RawCard] = Field(default_factory=list)
    search_ref: int | None = None
    search_value: str = ""
    submit_ref: int | None = None
    sort_ref: int | None = None
    sort_menu_ref: int | None = None
    next_ref: int | None = None
    file_refs: list[int] = Field(default_factory=list)
    signature: str = ""
