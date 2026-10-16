from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


class Element(BaseModel):
    """Интерактивный элемент страницы; ref — номер в атрибуте data-ta-ref текущего снимка."""

    ref: int
    tag: str = ""
    role: str = ""
    name: str = ""
    href: str | None = None
    placeholder: str | None = None
    input_type: str | None = None
    value: str | None = None
    download: bool = False

    @field_validator("tag", "role", "name", mode="before")
    @classmethod
    def _required_text(cls, value: object) -> str:
        return _text(value)

    @field_validator("href", "placeholder", "input_type", "value", mode="before")
    @classmethod
    def _optional(cls, value: object) -> str | None:
        return _optional_text(value)


class RawCard(BaseModel):
    """Запись выдачи в виде цитат со страницы, до разбора дат и цены."""

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
    """Снимок страницы: только факты, без выводов о том, где поиск, сортировка и т. п."""

    url: str
    title: str = ""
    # page — обычная страница; login / captcha / error — стоп-сигналы безопасности.
    page_kind: str = "page"
    text: str = ""
    elements: list[Element] = Field(default_factory=list)
    scroll: dict[str, int] = Field(default_factory=dict)  # y, height, viewport
    signature: str = ""
