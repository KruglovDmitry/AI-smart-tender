"""OpenAPI-facing tools for Open WebUI (documents + simple URL fetch)."""

from .document import list_directory, read_document, read_folder_documents
from .web import fetch_page

__all__ = [
    "list_directory",
    "read_document",
    "read_folder_documents",
    "fetch_page",
]
