"""Pydantic arg schemas for platform / DOM tools."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KeywordsInput(BaseModel):
    keywords: str = Field(description="Ключевые слова поиска")


class CardUrlInput(BaseModel):
    card_url: str = Field(description="Exact URL карточки из list_new_cards")


class DownloadDocInput(BaseModel):
    url: str = Field(description="Exact URL файла из list_tender_documents")
    name: str = Field(default="", description="Имя файла / текст ссылки")


class MarkProcessedInput(BaseModel):
    tender_id: str = Field(description="tender_id")
    tender_url: str = Field(default="", description="URL карточки")
    count_toward_limit: bool = Field(default=True)


class DomSnapshotInput(BaseModel):
    query: str | None = Field(
        default=None,
        description="Фильтр по тексту/placeholder/role; без query — до 150 приоритетных",
    )


class DomIdInput(BaseModel):
    el_id: int = Field(description="id из dom_snapshot")


class FillInput(BaseModel):
    el_id: int
    text: str
    submit: bool = Field(
        default=False,
        description="True — нажать Enter после ввода (отправка формы/поиска)",
    )


class NavigateInput(BaseModel):
    url: str


class FinishInput(BaseModel):
    summary: str
    success: bool = True
