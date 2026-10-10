from __future__ import annotations

from .generic import CARD_SELECTOR
from ..storage.repository import Repository, SiteProfile


def remember_success(repo: Repository, host: str, placeholder: str, structured_cards: bool) -> None:
    current = repo.load_profile(host) or SiteProfile(host=host)
    current.success_count += 1
    if placeholder:
        current.search_placeholder = placeholder
    if structured_cards:
        current.card_selector = CARD_SELECTOR
    repo.save_profile(current)
